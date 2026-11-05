from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import math_brain.mathnet_body_card_review_execution_v3 as execution
import math_brain.mathnet_body_card_review_signed_pair_v3 as signed_pair
import math_brain.mathnet_body_card_review_v3 as review
from math_brain.attempt_ledger import ArtifactPayload, AttemptLedger, SlotState
from math_brain.canonical_ed25519_v3 import CanonicalEd25519SignerV3
from math_brain.codex_runner import ExecutableProvenance
from math_brain.models import canonical_json


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def item_id(index: int) -> str:
    return f"pilot-curation:{index + 1:064x}"


def card_id(index: int) -> str:
    return f"pilot-card:{index + 1:064x}"


def ascii_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def synthetic_projection_value() -> dict[str, object]:
    return {
        "cards": [
            {
                "card_id": card_id(index),
                "guidance": f"Use verification method {index + 1} carefully.",
                "title": f"Method {index + 1}",
            }
            for index in range(review.BODY_CARD_FROZEN_CARD_COUNT_V3)
        ],
        "content_digest": digest("synthetic projection content"),
        "engineering_upper_bound_only": True,
        "gold_present": False,
        "items": [
            {
                "problem_text": f"Synthetic contest problem {index + 1}.",
                "review_item_id": item_id(index),
            }
            for index in range(review.BODY_CARD_FROZEN_ITEM_COUNT_V3)
        ],
        "schema": review.BODY_CARD_PROJECTION_SCHEMA_V3,
        "schema_version": review.BODY_CARD_REVIEW_VERSION_V3,
        "source_metadata_present": False,
    }


def encoded_projection(value: dict[str, object]) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


@pytest.fixture
def study(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    value = synthetic_projection_value()
    raw = encoded_projection(value)
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_RAW_DIGEST_V3",
        "sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_CONTENT_DIGEST_V3",
        value["content_digest"],
    )
    projection = review.load_frozen_body_card_projection_v3(raw)
    plan_a = review.build_body_card_review_plan_v3(
        projection,
        review.BodyCardReviewerSlotV3.REVIEWER_A,
    )
    plan_b = review.build_body_card_review_plan_v3(
        projection,
        review.BodyCardReviewerSlotV3.REVIEWER_B,
    )
    return SimpleNamespace(
        value=value,
        raw=raw,
        projection=projection,
        plan_a=plan_a,
        plan_b=plan_b,
    )


def decision_dict(
    opaque_id: str,
    *,
    language: str = "english",
    domain: str = "algebra",
    difficulty: str = "easy",
    applicability: str = "no_relevant_card",
    selected_card_id: str = "abstain",
) -> dict[str, str]:
    return {
        "applicability": applicability,
        "card_id": selected_card_id,
        "difficulty": difficulty,
        "domain": domain,
        "language": language,
        "review_item_id": opaque_id,
    }


def valid_output(plan: review.BodyCardReviewPlanV3) -> dict[str, object]:
    return {
        "reviews": [decision_dict(opaque_id) for opaque_id in plan.item_order_ids],
        "schema": review.BODY_CARD_OUTPUT_SCHEMA_V3,
        "schema_version": review.BODY_CARD_REVIEW_VERSION_V3,
    }


def executable_provenance() -> ExecutableProvenance:
    path = str(Path(sys.executable).resolve())
    return ExecutableProvenance(
        canonical_path=path,
        size_bytes=max(1, Path(path).stat().st_size),
        sha256_digest=digest("test executable"),
    )


def policy() -> review.BodyCardReviewRunPolicyV3:
    return review.BodyCardReviewRunPolicyV3(executable_provenance=executable_provenance())


def decisions_for_selection(
    projection: review.FrozenBodyCardProjectionV3,
    selected: dict[str, tuple[review.BodyCardDomainV3, review.BodyCardDifficultyV3]],
) -> tuple[review.BodyCardReviewDecisionV3, ...]:
    result = []
    for item in projection.items:
        cell = selected.get(item.review_item_id)
        if cell is None:
            result.append(
                review.BodyCardReviewDecisionV3(
                    review_item_id=item.review_item_id,
                    language=review.BodyCardLanguageV3.ENGLISH,
                    domain=review.BodyCardDomainV3.ALGEBRA,
                    difficulty=review.BodyCardDifficultyV3.EASY,
                    card_id="abstain",
                    applicability=review.BodyCardApplicabilityV3.NO_RELEVANT_CARD,
                )
            )
        else:
            domain, difficulty = cell
            result.append(
                review.BodyCardReviewDecisionV3(
                    review_item_id=item.review_item_id,
                    language=review.BodyCardLanguageV3.ENGLISH,
                    domain=domain,
                    difficulty=difficulty,
                    card_id=card_id(0),
                    applicability=review.BodyCardApplicabilityV3.RELEVANT,
                )
            )
    return tuple(result)


def fake_artifact(label: str, run_policy: review.BodyCardReviewRunPolicyV3) -> SimpleNamespace:
    ordinal = 1 if label == "a" else 2
    thread_ids = {
        "a": "019b3234-5678-7abc-8def-0123456789ab",
        "b": "019b3234-5678-7abc-9def-0123456789ac",
    }
    turn_ids = {
        "a": "019b4234-5678-7abc-8def-0123456789ab",
        "b": "019b4234-5678-7abc-9def-0123456789ac",
    }
    return SimpleNamespace(
        output_text=f"private-response-{label}",
        protocol_execution_id=digest(f"protocol {label}"),
        execution_nonce=f"{ordinal:064x}",
        thread_id=thread_ids[label],
        turn_id=turn_ids[label],
        transcript_hash=digest(f"transcript {label}"),
        stderr_hash=digest(f"stderr {label}"),
        runtime_directory_device=10,
        runtime_directory_inode=100 + ordinal,
        codex_home_directory_device=20,
        codex_home_directory_inode=200 + ordinal,
        codex_home_path_digest=digest(f"home path {label}"),
        executable_sha256_digest=run_policy.executable_provenance.sha256_digest,
        executable_size_bytes=run_policy.executable_provenance.size_bytes,
        command_tail_policy_digest=run_policy.command_policy_digest,
        solver_process_isolation_policy_digest=run_policy.process_policy_digest,
        requested_model=run_policy.requested_model,
        requested_provider=run_policy.requested_provider,
        requested_effort=run_policy.requested_effort,
        timeout_seconds=run_policy.timeout_seconds,
        audit=SimpleNamespace(
            accepted_model=run_policy.requested_model,
            accepted_provider=run_policy.requested_provider,
            accepted_effort=run_policy.requested_effort,
            model_identity_verified=False,
        ),
    )


def submission(
    *,
    slot: review.BodyCardReviewerSlotV3,
    decisions: tuple[review.BodyCardReviewDecisionV3, ...],
    study: SimpleNamespace,
    bound: execution.BoundBodyCardReviewsV3 | None = None,
) -> review.BodyCardReviewSubmissionV3:
    run_policy = policy()
    label = "a" if slot is review.BodyCardReviewerSlotV3.REVIEWER_A else "b"
    plan = study.plan_a if label == "a" else study.plan_b
    artifact = fake_artifact(label, run_policy)
    artifact_bytes = f"artifact-{label}".encode("ascii")
    slot_ordinal = 0 if label == "a" else 1
    attempt_slot_id = (
        f"slot_{(1 if label == 'a' else 2):032x}"
        if bound is None
        else bound.prepared.slots[slot_ordinal].slot_id
    )
    instance = object.__new__(review.BodyCardReviewSubmissionV3)
    values = {
        "reviewer_slot": slot,
        "decisions": decisions,
        "projection_raw_file_digest": study.projection.raw_file_digest,
        "projection_content_digest": study.projection.content_digest,
        "item_order_digest": plan.item_order_digest,
        "card_order_digest": plan.card_order_digest,
        "presentation_digest": plan.presentation_digest,
        "prompt_template_digest": plan.prompt_template_digest,
        "prompt_digest": plan.prompt_digest,
        "output_schema_digest": plan.output_schema_digest,
        "decision_universe_digest": review._decision_universe_digest_v3(decisions),
        "raw_response_digest": review._sha256(artifact.output_text.encode("utf-8")),
        "private_artifact_digest": review._sha256(artifact_bytes),
        "attempt_ledger_digest": (
            digest("test first-attempt ledger") if bound is None else bound.attempt_ledger_digest
        ),
        "attempt_slot_id": attempt_slot_id,
        "attempt_ordinal": slot_ordinal,
        "attempt_started_record_digest": digest(f"attempt started {label}"),
        "attempt_selection_record_digest": digest(f"attempt selection {label}"),
        "attempt_terminal_record_digest": digest(f"attempt terminal {label}"),
        "attempt_object_digest": digest(f"attempt object {label}"),
        "attempt_prerequisite_terminal_digest": (
            None if label == "a" else digest("attempt terminal a")
        ),
        "core_precommitment_digest": (
            digest("body/card core precommitment")
            if bound is None
            else bound.prepared.core_precommitment_digest
        ),
        "review_precommitment_digest": (
            digest("body/card final precommitment")
            if bound is None
            else bound.public_precommitment_digest
        ),
        "protocol_execution_id": artifact.protocol_execution_id,
        "execution_nonce": artifact.execution_nonce,
        "thread_id": artifact.thread_id,
        "turn_id": artifact.turn_id,
        "transcript_digest": artifact.transcript_hash,
        "stderr_digest": artifact.stderr_hash,
        "runtime_directory_device": artifact.runtime_directory_device,
        "runtime_directory_inode": artifact.runtime_directory_inode,
        "codex_home_directory_device": artifact.codex_home_directory_device,
        "codex_home_directory_inode": artifact.codex_home_directory_inode,
        "codex_home_path_digest": artifact.codex_home_path_digest,
        "executable_sha256_digest": artifact.executable_sha256_digest,
        "executable_size_bytes": artifact.executable_size_bytes,
        "command_policy_digest": artifact.command_tail_policy_digest,
        "process_policy_digest": artifact.solver_process_isolation_policy_digest,
        "request_policy_digest": run_policy.request_policy_digest,
        "requested_model": artifact.requested_model,
        "requested_provider": artifact.requested_provider,
        "requested_effort": artifact.requested_effort,
        "accepted_model": artifact.audit.accepted_model,
        "accepted_provider": artifact.audit.accepted_provider,
        "accepted_effort": artifact.audit.accepted_effort,
        "timeout_seconds": artifact.timeout_seconds,
        "model_identity_verified": artifact.audit.model_identity_verified,
        "_mint_marker": review._BODY_CARD_SUBMISSION_TOKEN_V3,
    }
    for name, value in values.items():
        object.__setattr__(instance, name, value)
    instance.validate_current()
    return instance


def bindings(
    study: SimpleNamespace,
    family_by_id: dict[str, str] | None = None,
) -> review.FrozenBodyCardEventFamilyBindingsV3:
    family_by_id = family_by_id or {}
    value = {
        "frozen_projection_content_digest": study.projection.content_digest,
        "frozen_projection_raw_file_digest": study.projection.raw_file_digest,
        "items": [
            {
                "event_family_digest": family_by_id.get(
                    item.review_item_id,
                    digest(f"family {item.review_item_id}"),
                ),
                "problem_text_digest": "sha256:"
                + hashlib.sha256(item.problem_text.encode("utf-8")).hexdigest(),
                "review_item_id": item.review_item_id,
            }
            for item in study.projection.items
        ],
        "schema": review.BODY_CARD_EVENT_BINDINGS_SCHEMA_V3,
        "schema_version": review.BODY_CARD_REVIEW_VERSION_V3,
    }
    raw = (canonical_json(value) + "\n").encode("utf-8")
    return review.load_body_card_event_family_bindings_v3(raw, study.projection)


KEY_A = digest("reviewer key A")
KEY_B = digest("reviewer key B")


def prepared_execution_state(
    study: SimpleNamespace,
    tmp_path: Path,
    *,
    event_family_bindings_digest: str,
) -> execution.PreparedBodyCardReviewsV3:
    material = execution.BodyCardReviewPrivateMaterialV3(
        b"\x03" * 32,
        b"\x01" * 32,
        b"\x02" * 32,
    )
    snapshot = execution.BodyCardReviewAuthSnapshotV3(
        canonical_path=str((tmp_path / "auth.json").resolve()),
        device=11,
        inode=12,
        size_bytes=13,
        content_commitment="hmac-sha256:" + "1" * 64,
    )
    return execution._prepare_body_card_reviews(
        study.projection,
        policy(),
        private_material=material,
        auth_snapshot=snapshot,
        repository_root=str(tmp_path.resolve()),
        implementation_revision="f" * 40,
        event_family_bindings_digest=event_family_bindings_digest,
        fixed_tokens=(
            ("slot_" + "1" * 32, digest("reviewer A dispatch nonce")),
            ("slot_" + "2" * 32, digest("reviewer B dispatch nonce")),
        ),
    )


def test_projection_payload_and_plans_are_blinded_deterministic_and_disjoint(
    study: SimpleNamespace,
) -> None:
    projection = study.projection
    assert len(projection.items) == 87
    assert len(projection.cards) == 20
    assert study.plan_a.item_order_ids != study.plan_b.item_order_ids
    assert study.plan_a.card_order_ids != study.plan_b.card_order_ids
    assert not any(
        left == right
        for left, right in zip(
            study.plan_a.item_order_ids,
            study.plan_b.item_order_ids,
            strict=True,
        )
    )
    assert (
        review.build_body_card_review_plan_v3(
            projection,
            review.BodyCardReviewerSlotV3.REVIEWER_A,
        )
        == study.plan_a
    )
    payload = json.loads(study.plan_a.payload_json)
    assert set(payload) == {
        "cards",
        "items",
        "projection_digest",
        "schema",
        "schema_version",
    }
    assert set(payload["items"][0]) == {"problem_text", "review_item_id"}
    assert set(payload["cards"][0]) == {"card_id", "guidance", "title"}
    serialized_payload = study.plan_a.payload_json.lower()
    for forbidden in (
        "event_family",
        "gold_present",
        "reviewer_a",
        "reviewer_b",
        "selection_coordinate",
        "source_ordinal",
    ):
        assert forbidden not in serialized_payload

    schema = json.loads(study.plan_a.output_schema_json)
    positions = schema["properties"]["reviews"]["prefixItems"]
    assert len(positions) == 87
    assert schema["properties"]["reviews"]["items"] is False
    assert study.plan_a.prompt_template_digest == (
        "sha256:7a242b0ddf8347635364880a77beca52c8a8665977c3146643d86b6373686753"
    )
    assert all(
        card.title not in study.plan_a.output_schema_json.decode() for card in projection.cards
    )


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda value: value.update({"gold": []}), "unexpected fields"),
        (lambda value: value["items"].pop(), "item universe"),
        (lambda value: value.update({"engineering_upper_bound_only": False}), "fixed fields"),
        (lambda value: value.update({"content_digest": digest("wrong")}), "embedded digest"),
        (
            lambda value: value["items"].__setitem__(1, dict(value["items"][0])),
            "unique and sorted",
        ),
    ],
)
def test_projection_semantic_mutations_fail_after_reanchored_transport_digest(
    monkeypatch: pytest.MonkeyPatch,
    mutation: object,
    message: str,
) -> None:
    value = synthetic_projection_value()
    mutation(value)
    raw = encoded_projection(value)
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_RAW_DIGEST_V3",
        "sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_CONTENT_DIGEST_V3",
        digest("synthetic projection content"),
    )
    with pytest.raises(ValueError, match=message):
        review.load_frozen_body_card_projection_v3(raw)


def test_projection_raw_mutation_duplicate_key_and_bom_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = synthetic_projection_value()
    raw = encoded_projection(value)
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_RAW_DIGEST_V3",
        "sha256:" + hashlib.sha256(raw).hexdigest(),
    )
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_CONTENT_DIGEST_V3",
        value["content_digest"],
    )
    changed = raw.replace(b"Synthetic contest problem 1", b"Synthetic contest problem X", 1)
    with pytest.raises(ValueError, match="raw digest"):
        review.load_frozen_body_card_projection_v3(changed)

    body = raw[:-1].decode("utf-8")
    duplicate = (
        body.replace(
            '"schema":',
            f'"schema":"{review.BODY_CARD_PROJECTION_SCHEMA_V3}","schema":',
            1,
        ).encode()
        + b"\n"
    )
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_RAW_DIGEST_V3",
        "sha256:" + hashlib.sha256(duplicate).hexdigest(),
    )
    with pytest.raises(ValueError, match="invalid JSON"):
        review.load_frozen_body_card_projection_v3(duplicate)

    bom = b"\xef\xbb\xbf" + raw
    monkeypatch.setattr(
        review,
        "BODY_CARD_FROZEN_PROJECTION_RAW_DIGEST_V3",
        "sha256:" + hashlib.sha256(bom).hexdigest(),
    )
    with pytest.raises(ValueError, match=r"framing|invalid JSON"):
        review.load_frozen_body_card_projection_v3(bom)


def test_complete_canonical_output_parses_and_normalizes(study: SimpleNamespace) -> None:
    raw = ascii_json(valid_output(study.plan_a))
    decisions = review.parse_body_card_review_output_v3(
        raw,
        study.projection,
        study.plan_a,
    )
    assert len(decisions) == 87
    assert tuple(item.review_item_id for item in decisions) == tuple(
        sorted(study.plan_a.item_order_ids)
    )
    with pytest.raises(TypeError, match="strict run adapter"):
        review.BodyCardReviewSubmissionV3()


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda root, plan: root["reviews"].pop(), "full presentation"),
        (lambda root, plan: root["reviews"].reverse(), "order differs"),
        (
            lambda root, plan: root["reviews"].__setitem__(1, dict(root["reviews"][0])),
            "order differs",
        ),
        (lambda root, plan: root.update({"rationale": "forbidden"}), "unexpected fields"),
        (
            lambda root, plan: root["reviews"][0].update({"language": "maybe"}),
            "decision is invalid",
        ),
        (
            lambda root, plan: root["reviews"][0].update(
                {"applicability": "relevant", "card_id": "abstain"}
            ),
            "decision is invalid",
        ),
        (
            lambda root, plan: root["reviews"][0].update(
                {"applicability": "uncertain", "card_id": plan.card_order_ids[0]}
            ),
            "decision is invalid",
        ),
    ],
)
def test_output_parser_rejects_partial_reordered_extra_and_inconsistent_data(
    study: SimpleNamespace,
    mutation: object,
    message: str,
) -> None:
    root = valid_output(study.plan_a)
    mutation(root, study.plan_a)
    with pytest.raises(ValueError, match=message):
        review.parse_body_card_review_output_v3(
            ascii_json(root),
            study.projection,
            study.plan_a,
        )


def test_output_parser_rejects_noncanonical_and_duplicate_json(study: SimpleNamespace) -> None:
    root = valid_output(study.plan_a)
    canonical = ascii_json(root)
    with pytest.raises(ValueError, match="not canonical"):
        review.parse_body_card_review_output_v3(
            canonical + "\n",
            study.projection,
            study.plan_a,
        )
    duplicate = canonical.replace(
        '"schema":',
        f'"schema":"{review.BODY_CARD_OUTPUT_SCHEMA_V3}","schema":',
        1,
    )
    with pytest.raises(ValueError, match="invalid JSON"):
        review.parse_body_card_review_output_v3(
            duplicate,
            study.projection,
            study.plan_a,
        )


def test_pair_gate_accepts_role_specific_schema_digests_and_rejects_reuse(
    study: SimpleNamespace,
) -> None:
    decisions = decisions_for_selection(study.projection, {})
    reviewer_a = submission(
        slot=review.BodyCardReviewerSlotV3.REVIEWER_A,
        decisions=decisions,
        study=study,
    )
    reviewer_b = submission(
        slot=review.BodyCardReviewerSlotV3.REVIEWER_B,
        decisions=decisions,
        study=study,
    )
    assert reviewer_a.output_schema_digest != reviewer_b.output_schema_digest
    review.validate_body_card_reviewer_pair_v3(
        reviewer_a,
        reviewer_b,
        reviewer_a_key_fingerprint=KEY_A,
        reviewer_b_key_fingerprint=KEY_B,
    )
    object.__setattr__(reviewer_b, "execution_nonce", reviewer_a.execution_nonce)
    with pytest.raises(ValueError, match="execution_nonce"):
        review.validate_body_card_reviewer_pair_v3(
            reviewer_a,
            reviewer_b,
            reviewer_a_key_fingerprint=KEY_A,
            reviewer_b_key_fingerprint=KEY_B,
        )


def selection_pair(
    study: SimpleNamespace,
) -> tuple[
    review.BodyCardReviewSubmissionV3,
    review.BodyCardReviewSubmissionV3,
    dict[str, str],
    list[str],
]:
    candidate_ids = [item_id(index) for index in range(13)]
    cell_by_id = {
        candidate_ids[0]: review.BODY_CARD_CELL_ORDER_V3[0],
        candidate_ids[1]: review.BODY_CARD_CELL_ORDER_V3[0],
        **{
            candidate_ids[cell_index + 1]: review.BODY_CARD_CELL_ORDER_V3[cell_index]
            for cell_index in range(1, 12)
        },
    }
    decisions = decisions_for_selection(study.projection, cell_by_id)
    reviewer_a = submission(
        slot=review.BodyCardReviewerSlotV3.REVIEWER_A,
        decisions=decisions,
        study=study,
    )
    reviewer_b = submission(
        slot=review.BodyCardReviewerSlotV3.REVIEWER_B,
        decisions=decisions,
        study=study,
    )
    family_by_id = {
        candidate_ids[0]: digest("shared trap family"),
        candidate_ids[1]: digest("alternate first-cell family"),
        candidate_ids[2]: digest("shared trap family"),
        **{
            candidate_ids[index]: digest(f"unique selected family {index}")
            for index in range(3, 13)
        },
    }
    return reviewer_a, reviewer_b, family_by_id, candidate_ids


def test_selector_escapes_greedy_trap_and_is_lexicographically_deterministic(
    study: SimpleNamespace,
) -> None:
    reviewer_a, reviewer_b, family_by_id, candidate_ids = selection_pair(study)
    frozen_bindings = bindings(study, family_by_id)
    report = review._select_body_card_reviews_v3(
        reviewer_a,
        reviewer_b,
        frozen_bindings,
        reviewer_a_key_fingerprint=KEY_A,
        reviewer_b_key_fingerprint=KEY_B,
        expected_event_family_bindings_digest=frozen_bindings.raw_file_digest,
        review_precommitment_digest=digest("review precommitment"),
    )
    assert report.status is review.BodyCardSelectionStatusV3.FEASIBLE
    assert len(report.selected) == 12
    assert report.selected[0].review_item_id == candidate_ids[1]
    assert report.selected[1].review_item_id == candidate_ids[2]
    assert len({item.event_family_digest for item in report.selected}) == 12
    assert {item.card_id for item in report.selected} == {card_id(0)}
    assert report.engineering_upper_bound_only is True
    assert report.automatic_retrieval_evaluation is False
    assert report.confirmatory_claim_authorized is False


def test_selector_never_partially_fills_hall_conflict(study: SimpleNamespace) -> None:
    reviewer_a, reviewer_b, family_by_id, candidate_ids = selection_pair(study)
    family_by_id[candidate_ids[1]] = family_by_id[candidate_ids[0]]
    frozen_bindings = bindings(study, family_by_id)
    report = review._select_body_card_reviews_v3(
        reviewer_a,
        reviewer_b,
        frozen_bindings,
        reviewer_a_key_fingerprint=KEY_A,
        reviewer_b_key_fingerprint=KEY_B,
        expected_event_family_bindings_digest=frozen_bindings.raw_file_digest,
        review_precommitment_digest=digest("review precommitment"),
    )
    assert report.status is review.BodyCardSelectionStatusV3.INFEASIBLE
    assert report.selected == ()


def test_one_axis_disagreement_excludes_candidate(study: SimpleNamespace) -> None:
    reviewer_a, reviewer_b, family_by_id, candidate_ids = selection_pair(study)
    altered = list(reviewer_b.decisions)
    index = next(
        index for index, item in enumerate(altered) if item.review_item_id == candidate_ids[12]
    )
    original = altered[index]
    altered[index] = review.BodyCardReviewDecisionV3(
        review_item_id=original.review_item_id,
        language=review.BodyCardLanguageV3.UNCERTAIN,
        domain=original.domain,
        difficulty=original.difficulty,
        card_id=original.card_id,
        applicability=original.applicability,
    )
    reviewer_b_changed = submission(
        slot=review.BodyCardReviewerSlotV3.REVIEWER_B,
        decisions=tuple(altered),
        study=study,
    )
    frozen_bindings = bindings(study, family_by_id)
    report = review._select_body_card_reviews_v3(
        reviewer_a,
        reviewer_b_changed,
        frozen_bindings,
        reviewer_a_key_fingerprint=KEY_A,
        reviewer_b_key_fingerprint=KEY_B,
        expected_event_family_bindings_digest=frozen_bindings.raw_file_digest,
        review_precommitment_digest=digest("review precommitment"),
    )
    assert report.status is review.BodyCardSelectionStatusV3.INFEASIBLE
    assert report.selected == ()


def test_signed_pair_and_public_precommitment_bind_distinct_underlying_keys(
    study: SimpleNamespace,
    tmp_path: Path,
) -> None:
    initial_a, initial_b, family_by_id, _ = selection_pair(study)
    frozen_bindings = bindings(study, family_by_id)
    private_material = execution.BodyCardReviewPrivateMaterialV3(
        b"\x03" * 32,
        b"\x01" * 32,
        b"\x02" * 32,
    )
    auth_snapshot = execution.BodyCardReviewAuthSnapshotV3(
        canonical_path=str((tmp_path / "auth.json").resolve()),
        device=1,
        inode=2,
        size_bytes=3,
        content_commitment="hmac-sha256:" + "1" * 64,
    )
    prepared = execution._prepare_body_card_reviews(
        study.projection,
        policy(),
        private_material=private_material,
        auth_snapshot=auth_snapshot,
        repository_root=str(tmp_path.resolve()),
        implementation_revision="f" * 40,
        event_family_bindings_digest=frozen_bindings.raw_file_digest,
        fixed_tokens=(
            ("slot_" + "1" * 32, digest("reviewer A dispatch nonce")),
            ("slot_" + "2" * 32, digest("reviewer B dispatch nonce")),
        ),
    )
    private_state_path = tmp_path / "private-state.json"
    execution.save_body_card_review_private_state_v3(private_state_path, prepared)
    ledger = execution.create_body_card_review_ledger_v3(prepared, tmp_path / "ledger")
    bound = execution.bind_body_card_review_ledger_v3(
        prepared,
        ledger,
        private_state_path=private_state_path,
    )
    reviewer_a = submission(
        slot=review.BodyCardReviewerSlotV3.REVIEWER_A,
        decisions=initial_a.decisions,
        study=study,
        bound=bound,
    )
    reviewer_b = submission(
        slot=review.BodyCardReviewerSlotV3.REVIEWER_B,
        decisions=initial_b.decisions,
        study=study,
        bound=bound,
    )
    signer_a = CanonicalEd25519SignerV3(
        b"\x01" * 32,
        domain=review.BODY_CARD_REVIEWER_A_SIGNING_DOMAIN_V3,
    )
    signer_b = CanonicalEd25519SignerV3(
        b"\x02" * 32,
        domain=review.BODY_CARD_REVIEWER_B_SIGNING_DOMAIN_V3,
    )
    manifest_a = review.sign_body_card_review_submission_v3(reviewer_a, signer_a)
    manifest_b = review.sign_body_card_review_submission_v3(reviewer_b, signer_b)
    precommitment = json.loads(ascii_json(bound.public_precommitment))
    precommitment_digest = review.body_card_review_public_precommitment_digest_v3(precommitment)
    precommitment_bytes = ascii_json(precommitment).encode("utf-8") + b"\n"
    report = review.verify_and_select_body_card_reviews_v3(
        manifest_a,
        manifest_b,
        bindings=frozen_bindings,
        review_precommitment_bytes=precommitment_bytes,
        expected_review_precommitment_digest=precommitment_digest,
    )
    assert report.status is review.BodyCardSelectionStatusV3.FEASIBLE
    pair_bytes = signed_pair.serialize_signed_body_card_review_pair_v3(
        manifest_a,
        manifest_b,
        bindings=frozen_bindings,
        review_precommitment_bytes=precommitment_bytes,
        expected_review_precommitment_digest=precommitment_digest,
    )
    assert (
        signed_pair.verify_and_select_signed_body_card_review_pair_bytes_v3(
            pair_bytes,
            bindings=frozen_bindings,
            review_precommitment_bytes=precommitment_bytes,
            expected_review_precommitment_digest=precommitment_digest,
        )
        == report
    )
    tampered_pair = json.loads(pair_bytes)
    signature = tampered_pair["reviewer_b"]["signature"]
    tampered_pair["reviewer_b"]["signature"] = signature[:-1] + (
        "0" if signature[-1] != "0" else "1"
    )
    with pytest.raises(ValueError, match="signature"):
        signed_pair.verify_and_select_signed_body_card_review_pair_bytes_v3(
            ascii_json(tampered_pair).encode("ascii") + b"\n",
            bindings=frozen_bindings,
            review_precommitment_bytes=precommitment_bytes,
            expected_review_precommitment_digest=precommitment_digest,
        )

    posthoc_a = CanonicalEd25519SignerV3(
        b"\x03" * 32,
        domain=review.BODY_CARD_REVIEWER_A_SIGNING_DOMAIN_V3,
    )
    posthoc_b = CanonicalEd25519SignerV3(
        b"\x04" * 32,
        domain=review.BODY_CARD_REVIEWER_B_SIGNING_DOMAIN_V3,
    )
    with pytest.raises(ValueError, match=r"unexpected reviewer key|precommitment"):
        review.verify_and_select_body_card_reviews_v3(
            review.sign_body_card_review_submission_v3(reviewer_a, posthoc_a),
            review.sign_body_card_review_submission_v3(reviewer_b, posthoc_b),
            bindings=frozen_bindings,
            review_precommitment_bytes=precommitment_bytes,
            expected_review_precommitment_digest=precommitment_digest,
        )

    tampered_rule = json.loads(canonical_json(precommitment))
    tampered_rule["core"]["selection"] = {"rule": "operator-choice"}
    tampered_rule["core_digest"] = execution.body_card_review_core_precommitment_digest_v3(
        tampered_rule["core"]
    )
    with pytest.raises(ValueError, match="protocol rules differ"):
        review.verify_and_select_body_card_reviews_v3(
            manifest_a,
            manifest_b,
            bindings=frozen_bindings,
            review_precommitment_bytes=ascii_json(tampered_rule).encode("utf-8") + b"\n",
            expected_review_precommitment_digest=(
                review.body_card_review_public_precommitment_digest_v3(tampered_rule)
            ),
        )

    type_confused = json.loads(canonical_json(precommitment))
    type_confused["schema_version"] = True
    with pytest.raises(ValueError, match="final review precommitment schema differs"):
        review.verify_and_select_body_card_reviews_v3(
            manifest_a,
            manifest_b,
            bindings=frozen_bindings,
            review_precommitment_bytes=ascii_json(type_confused).encode("utf-8") + b"\n",
            expected_review_precommitment_digest=(
                review.body_card_review_public_precommitment_digest_v3(type_confused)
            ),
        )

    nonfinite = json.loads(canonical_json(precommitment))
    nonfinite["core"]["reviewer_processes"] = float("nan")
    with pytest.raises(ValueError):
        review.body_card_review_public_precommitment_digest_v3(nonfinite)
    assert precommitment["core"]["automatic_retrieval_evaluation"] is False
    assert precommitment["core"]["engineering_upper_bound_only"] is True
    assert precommitment["core"]["served_model_identity_verified"] is False
    assert precommitment["core"]["run_policy"]["no_retry"] is True
    assert (
        precommitment["core"]["reviewer_a"]["output_schema_digest"]
        != (precommitment["core"]["reviewer_b"]["output_schema_digest"])
    )
    assert precommitment_digest.startswith("sha256:")

    reused_key_b = CanonicalEd25519SignerV3(
        b"\x01" * 32,
        domain=review.BODY_CARD_REVIEWER_B_SIGNING_DOMAIN_V3,
    )
    assert signer_a.key_fingerprint == reused_key_b.key_fingerprint
    with pytest.raises(ValueError, match="distinct exact reviewer verifiers"):
        execution.build_body_card_review_core_precommitment_v3(
            study.projection,
            study.plan_a,
            study.plan_b,
            policy(),
            reviewer_a_verifier=signer_a.verifier(),
            reviewer_b_verifier=reused_key_b.verifier(),
            event_family_bindings_digest=frozen_bindings.raw_file_digest,
            auth_snapshot_commitment=prepared.auth_snapshot.content_commitment,
            implementation_revision="f" * 40,
        )

    wrong_domain_signer = CanonicalEd25519SignerV3(
        b"\x03" * 32,
        domain="org.example/wrong-reviewer-domain/v1",
    )
    with pytest.raises(ValueError, match="wrong reviewer domain"):
        review.sign_body_card_review_submission_v3(reviewer_a, wrong_domain_signer)


def test_execution_private_state_replays_binds_and_public_payload_is_text_free(
    study: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_bindings = bindings(study)
    prepared = prepared_execution_state(
        study,
        tmp_path,
        event_family_bindings_digest=frozen_bindings.raw_file_digest,
    )
    prepared.validate_current()
    private_bytes = execution.serialize_body_card_review_private_state_v3(prepared)
    restored = execution.deserialize_body_card_review_private_state_v3(
        private_bytes,
        study.projection,
    )
    assert restored == prepared

    private_path = tmp_path / "private-state.json"
    execution.save_body_card_review_private_state_v3(private_path, prepared)
    assert (
        execution.load_body_card_review_private_state_v3(
            private_path,
            study.projection,
        )
        == prepared
    )
    with pytest.raises(FileExistsError):
        execution.save_body_card_review_private_state_v3(private_path, prepared)

    ledger = execution.create_body_card_review_ledger_v3(prepared, tmp_path / "ledger")
    bound = execution.bind_body_card_review_ledger_v3(
        prepared,
        ledger,
        private_state_path=private_path,
    )
    monkeypatch.setattr(
        execution,
        "_validate_live_inputs",
        lambda *_args, **_kwargs: b"{}",
    )
    receipt_path = tmp_path / "publication-receipt.json"
    public_bytes = execution.finalize_body_card_review_publication_v3(
        bound,
        ledger=ledger,
        repository_root=tmp_path,
        auth_file=tmp_path / "auth.json",
        publication_receipt_path=receipt_path,
    )
    assert receipt_path.is_file()
    assert (
        execution.finalize_body_card_review_publication_v3(
            bound,
            ledger=ledger,
            repository_root=tmp_path,
            auth_file=tmp_path / "auth.json",
            publication_receipt_path=receipt_path,
        )
        == public_bytes
    )
    parsed = review.parse_body_card_review_public_precommitment_v3(
        public_bytes,
        expected_digest=bound.public_precommitment_digest,
    )
    assert parsed == bound.public_precommitment
    assert parsed["attempt_ledger"]["slot_count"] == 2
    assert parsed["core"]["auth_continuity_commitment"] == (
        prepared.auth_snapshot.content_commitment
    )
    for forbidden in (
        prepared.private_material.ledger_key.hex(),
        prepared.private_material.reviewer_a_seed.hex(),
        prepared.private_material.reviewer_b_seed.hex(),
        prepared.auth_snapshot.canonical_path,
        "BEGIN_REVIEW_PAYLOAD_JSON",
        study.projection.items[0].problem_text,
    ):
        assert forbidden.encode("utf-8") not in public_bytes

    reopened = execution.open_body_card_review_ledger_v3(prepared, ledger.root)
    rebound = execution.restore_bound_body_card_review_ledger_v3(
        restored,
        reopened,
        private_state_path=private_path,
        public_precommitment_bytes=public_bytes,
        expected_public_precommitment_digest=bound.public_precommitment_digest,
    )
    assert rebound == bound
    assert execution._validate_publication_receipt(rebound, reopened, receipt_path) == (
        receipt_path
    )
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setattr(
        execution,
        "_validate_current_implementation",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(ValueError, match="existing file"):
        execution.execute_body_card_reviews_v3(
            rebound,
            ledger=reopened,
            public_precommitment_bytes=public_bytes,
            expected_public_precommitment_digest=bound.public_precommitment_digest,
            publication_receipt_path=tmp_path / "missing-receipt.json",
            runtime_parent=runtime_root,
            repository_root=tmp_path,
            auth_file=tmp_path / "auth.json",
        )
    tampered_receipt = bytearray(receipt_path.read_bytes())
    tampered_receipt[-1] = ord(" ")
    receipt_path.write_bytes(bytes(tampered_receipt))
    with pytest.raises(ValueError, match=r"invalid JSON|canonical|differs"):
        execution._validate_publication_receipt(rebound, reopened, receipt_path)


def test_private_state_atomic_write_failure_leaves_no_final_or_temp_file(
    study: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = prepared_execution_state(
        study,
        tmp_path,
        event_family_bindings_digest=digest("event bindings"),
    )
    target = tmp_path / "private-state.json"
    real_write = execution.os.write
    calls = 0

    def fail_after_progress(descriptor: int, raw: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_write(descriptor, raw[:1])
            raise OSError("synthetic private-state write failure")
        return real_write(descriptor, raw)

    monkeypatch.setattr(execution.os, "write", fail_after_progress)
    with pytest.raises(OSError, match="synthetic private-state write failure"):
        execution.save_body_card_review_private_state_v3(target, prepared)
    assert not target.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_execution_rejects_in_repo_wrong_and_extra_ledgers_before_start(
    study: SimpleNamespace,
    tmp_path: Path,
) -> None:
    frozen_bindings = bindings(study)
    prepared = prepared_execution_state(
        study,
        tmp_path,
        event_family_bindings_digest=frozen_bindings.raw_file_digest,
    )
    private_state_path = tmp_path / "private-state.json"
    execution.save_body_card_review_private_state_v3(private_state_path, prepared)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    with pytest.raises(ValueError, match="outside every Git repository"):
        execution.create_body_card_review_ledger_v3(prepared, repository / "ledger")
    with pytest.raises(ValueError, match="outside every Git repository"):
        execution.save_body_card_review_private_state_v3(
            repository / "private.json",
            prepared,
        )

    wrong_first = replace(
        prepared.commitments[0],
        batch_commitment="hmac-sha256:" + "9" * 64,
    )
    wrong_ledger = AttemptLedger.create(
        tmp_path / "wrong-ledger",
        key=prepared.private_material.ledger_key,
        slots=(wrong_first, prepared.commitments[1]),
    )
    with pytest.raises(ValueError, match="slot universe differs"):
        execution.bind_body_card_review_ledger_v3(
            prepared,
            wrong_ledger,
            private_state_path=private_state_path,
        )
    assert all(wrong_ledger.state(slot.slot_id) is SlotState.PENDING for slot in wrong_ledger.slots)

    third_id = "slot_" + "3" * 32
    third = prepared.slots[0].dispatch.seal(
        key=prepared.private_material.ledger_key,
        slot_id=third_id,
    )
    extra_ledger = AttemptLedger.create(
        tmp_path / "extra-ledger",
        key=prepared.private_material.ledger_key,
        slots=(*prepared.commitments, third),
    )
    with pytest.raises(ValueError, match="slot universe differs"):
        execution.bind_body_card_review_ledger_v3(
            prepared,
            extra_ledger,
            private_state_path=private_state_path,
        )
    assert not list((extra_ledger.root / "transitions").iterdir())


def test_execution_causal_history_survives_reopen_and_never_starts_b_early(
    study: SimpleNamespace,
    tmp_path: Path,
) -> None:
    prepared = prepared_execution_state(
        study,
        tmp_path,
        event_family_bindings_digest=digest("event bindings"),
    )
    ledger = execution.create_body_card_review_ledger_v3(prepared, tmp_path / "ledger")
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime_parent = tmp_path / "runtime"
    runtime_parent.mkdir()
    calls: list[str] = []

    def fake_runner(_runtime: Path, dispatch: object) -> ArtifactPayload:
        calls.append(dispatch.dispatch_nonce)
        return ArtifactPayload(b'{"ok":true}')

    with pytest.raises(Exception, match="exact preceding RETURNED"):
        ledger.execute_first_attempt_after(
            prepared.slots[1].slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository,
            dispatch=prepared.slots[1].dispatch,
            prerequisite_terminal=None,
            runner=fake_runner,
        )
    assert calls == []
    assert list(runtime_parent.iterdir()) == []
    assert ledger.state(prepared.slots[1].slot_id) is SlotState.PENDING

    terminal_a = ledger.execute_first_attempt_after(
        prepared.slots[0].slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository,
        dispatch=prepared.slots[0].dispatch,
        prerequisite_terminal=None,
        runner=fake_runner,
    )
    assert execution._validate_slot_history(prepared, ledger, ordinal=0) == terminal_a
    terminal_b = ledger.execute_first_attempt_after(
        prepared.slots[1].slot_id,
        runtime_parent=runtime_parent,
        repository_root=repository,
        dispatch=prepared.slots[1].dispatch,
        prerequisite_terminal=terminal_a,
        runner=fake_runner,
    )
    assert len(calls) == 2
    reopened = execution.open_body_card_review_ledger_v3(prepared, ledger.root)
    assert execution._validate_slot_history(prepared, reopened, ordinal=1) == terminal_b
    assert (
        reopened.started_attempt(prepared.slots[1].slot_id).prerequisite_terminal_digest
        == terminal_a.record_digest
    )


def test_execute_success_path_returns_signed_pair_and_cold_replays(
    study: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_a, initial_b, family_by_id, _candidate_ids = selection_pair(study)
    frozen_bindings = bindings(study, family_by_id)
    prepared = prepared_execution_state(
        study,
        tmp_path,
        event_family_bindings_digest=frozen_bindings.raw_file_digest,
    )
    private_path = tmp_path / "private-state.json"
    execution.save_body_card_review_private_state_v3(private_path, prepared)
    ledger = execution.create_body_card_review_ledger_v3(prepared, tmp_path / "ledger")
    bound = execution.bind_body_card_review_ledger_v3(
        prepared,
        ledger,
        private_state_path=private_path,
    )
    monkeypatch.setattr(
        execution,
        "_validate_live_inputs",
        lambda *_args, **_kwargs: b"{}",
    )
    monkeypatch.setattr(
        execution,
        "_validate_current_implementation",
        lambda *_args, **_kwargs: None,
    )
    receipt_path = tmp_path / "publication-receipt.json"
    public_bytes = execution.finalize_body_card_review_publication_v3(
        bound,
        ledger=ledger,
        repository_root=tmp_path,
        auth_file=tmp_path / "auth.json",
        publication_receipt_path=receipt_path,
    )
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime_parent = tmp_path / "runtime"
    runtime_parent.mkdir()
    reviewer_a = submission(
        slot=review.BodyCardReviewerSlotV3.REVIEWER_A,
        decisions=initial_a.decisions,
        study=study,
        bound=bound,
    )
    reviewer_b = submission(
        slot=review.BodyCardReviewerSlotV3.REVIEWER_B,
        decisions=initial_b.decisions,
        study=study,
        bound=bound,
    )
    calls: list[review.BodyCardReviewerSlotV3] = []

    def fake_run_native_slot(**kwargs: object) -> object:
        slot = kwargs["slot"]
        active_ledger = kwargs["ledger"]
        calls.append(slot.reviewer_slot)
        return active_ledger.execute_first_attempt_after(
            slot.slot_id,
            runtime_parent=kwargs["runtime_parent"],
            repository_root=kwargs["repository_root"],
            dispatch=slot.dispatch,
            prerequisite_terminal=kwargs["prerequisite_terminal"],
            runner=lambda _runtime, _dispatch: ArtifactPayload(
                b'{"private":"artifact"}',
                execution.BODY_CARD_PRIVATE_ARTIFACT_MEDIA_TYPE_V3,
            ),
        )

    monkeypatch.setattr(execution, "_run_native_slot", fake_run_native_slot)
    monkeypatch.setattr(execution, "deserialize_codex_run_result", lambda _raw: object())
    monkeypatch.setattr(
        execution,
        "bind_body_card_review_attempt_v3",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        execution,
        "parse_body_card_review_run_v3",
        lambda _artifact, _projection, plan, _policy, _attempt, **_kwargs: (
            reviewer_a
            if plan.reviewer_slot is review.BodyCardReviewerSlotV3.REVIEWER_A
            else reviewer_b
        ),
    )
    manifest_a, manifest_b = execution.execute_body_card_reviews_v3(
        bound,
        ledger=ledger,
        public_precommitment_bytes=public_bytes,
        expected_public_precommitment_digest=bound.public_precommitment_digest,
        publication_receipt_path=receipt_path,
        runtime_parent=runtime_parent,
        repository_root=repository,
        auth_file=tmp_path / "auth.json",
    )
    assert calls == [
        review.BodyCardReviewerSlotV3.REVIEWER_A,
        review.BodyCardReviewerSlotV3.REVIEWER_B,
    ]
    assert all(ledger.state(slot.slot_id) is SlotState.RETURNED for slot in prepared.slots)
    pair_bytes = signed_pair.serialize_signed_body_card_review_pair_v3(
        manifest_a,
        manifest_b,
        bindings=frozen_bindings,
        review_precommitment_bytes=public_bytes,
        expected_review_precommitment_digest=bound.public_precommitment_digest,
    )
    report = signed_pair.verify_and_select_signed_body_card_review_pair_bytes_v3(
        pair_bytes,
        bindings=frozen_bindings,
        review_precommitment_bytes=public_bytes,
        expected_review_precommitment_digest=bound.public_precommitment_digest,
    )
    assert report.status is review.BodyCardSelectionStatusV3.FEASIBLE


def test_execute_stops_before_b_when_private_state_disappears_after_a(
    study: SimpleNamespace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_bindings = bindings(study)
    prepared = prepared_execution_state(
        study,
        tmp_path,
        event_family_bindings_digest=frozen_bindings.raw_file_digest,
    )
    private_path = tmp_path / "private-state.json"
    execution.save_body_card_review_private_state_v3(private_path, prepared)
    ledger = execution.create_body_card_review_ledger_v3(prepared, tmp_path / "ledger")
    bound = execution.bind_body_card_review_ledger_v3(
        prepared,
        ledger,
        private_state_path=private_path,
    )
    monkeypatch.setattr(
        execution,
        "_validate_live_inputs",
        lambda *_args, **_kwargs: b"{}",
    )
    monkeypatch.setattr(
        execution,
        "_validate_current_implementation",
        lambda *_args, **_kwargs: None,
    )
    receipt_path = tmp_path / "publication-receipt.json"
    public_bytes = execution.finalize_body_card_review_publication_v3(
        bound,
        ledger=ledger,
        repository_root=tmp_path,
        auth_file=tmp_path / "auth.json",
        publication_receipt_path=receipt_path,
    )
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    runtime_parent = tmp_path / "runtime"
    runtime_parent.mkdir()
    calls: list[review.BodyCardReviewerSlotV3] = []

    def fake_run_native_slot(**kwargs: object) -> object:
        slot = kwargs["slot"]
        active_ledger = kwargs["ledger"]
        calls.append(slot.reviewer_slot)
        terminal = active_ledger.execute_first_attempt_after(
            slot.slot_id,
            runtime_parent=kwargs["runtime_parent"],
            repository_root=kwargs["repository_root"],
            dispatch=slot.dispatch,
            prerequisite_terminal=kwargs["prerequisite_terminal"],
            runner=lambda _runtime, _dispatch: ArtifactPayload(b'{"private":"artifact"}'),
        )
        if slot.reviewer_slot is review.BodyCardReviewerSlotV3.REVIEWER_A:
            private_path.unlink()
        return terminal

    monkeypatch.setattr(execution, "_run_native_slot", fake_run_native_slot)
    with pytest.raises(ValueError, match="private state"):
        execution.execute_body_card_reviews_v3(
            bound,
            ledger=ledger,
            public_precommitment_bytes=public_bytes,
            expected_public_precommitment_digest=bound.public_precommitment_digest,
            publication_receipt_path=receipt_path,
            runtime_parent=runtime_parent,
            repository_root=repository,
            auth_file=tmp_path / "auth.json",
        )
    assert calls == [review.BodyCardReviewerSlotV3.REVIEWER_A]
    assert ledger.state(prepared.slots[0].slot_id) is SlotState.RETURNED
    assert ledger.state(prepared.slots[1].slot_id) is SlotState.PENDING
