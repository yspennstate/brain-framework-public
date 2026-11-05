from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import math_brain.challenge_study as challenge_study_module
from math_brain.attempt_ledger import ArtifactPayload, LedgerFormatError
from math_brain.challenge_study import (
    SOLVER_PROMPT,
    StudySecrets,
    open_preregistered_holdout_study,
    preregister_holdout_study,
)
from math_brain.evaluation import Condition, EvaluationConfig


ROOT = Path(__file__).parents[1]


def test_challenge_preregistration_disables_confounded_localization_claim() -> None:
    config = EvaluationConfig.from_toml(ROOT / "config" / "challenge_evaluation.toml")
    assert config.pair_count == 10
    assert config.runtime.requested_model == "gpt-5.6-sol"
    assert config.runtime.requested_provider == "openai"
    assert config.runtime.requested_reasoning_effort == "ultra"
    assert config.weights.localization == 0.0
    assert config.weights.balanced_validity == 0.45
    assert sum(config.weights.to_dict().values()) == pytest.approx(1.0)
    assert "typed_payload" in SOLVER_PROMPT
    assert "experimental conditions" in SOLVER_PROMPT
    assert "H01" not in SOLVER_PROMPT and "D01" not in SOLVER_PROMPT


def test_study_secrets_are_separate_fresh_caller_held_values() -> None:
    left = StudySecrets.generate()
    right = StudySecrets.generate()
    assert len(left.runtime_salt) == 32
    assert len(left.ledger_key) == 32
    assert left.runtime_salt != left.ledger_key
    assert left.runtime_salt != right.runtime_salt
    assert left.ledger_key != right.ledger_key
    assert left.seeds.commitments() != right.seeds.commitments()
    assert len(set(left.seeds.commitments().to_dict().values())) == 5


def test_private_codex_artifact_is_opaque_to_the_generic_ledger_json_parser() -> None:
    encoded = (
        b'{"stderr":{"data":"","encoding":"base64"},"transcript":{"data":"'
        + (b"a" * 100_000)
        + b'","encoding":"base64"}}'
    )
    payload = ArtifactPayload(
        encoded,
        challenge_study_module._CODEX_ARTIFACT_MEDIA_TYPE,
    )
    assert payload.content == encoded
    assert not payload.media_type.endswith("+json")

    with pytest.raises(LedgerFormatError):
        ArtifactPayload(
            encoded,
            "application/vnd.math-research-brain.private-codex-run-result+json",
        )


def test_private_holdout_plan_when_evaluator_inputs_are_explicitly_available() -> None:
    payload_name = os.environ.get("MATH_BRAIN_HOLDOUT_PAYLOAD")
    executable = os.environ.get("MATH_BRAIN_CODEX_EXE")
    if not payload_name or not executable:
        pytest.skip("private holdout and exact native Codex path are evaluator-local")
    payload_path = Path(payload_name)
    if not payload_path.is_file() or not Path(executable).is_file():
        pytest.skip("private evaluator inputs are unavailable")

    secrets_material = StudySecrets.generate()
    preregistration = preregister_holdout_study(
        challenge_config=ROOT / "config" / "challenge_bank.json",
        evaluation_config=ROOT / "config" / "challenge_evaluation.toml",
        executable_path=executable,
        brain_revision="0" * 40,
        secrets_material=secrets_material,
    )
    prereg_record = preregistration.private_preregistration_record(secrets_material)
    prereg_encoded = json.dumps(prereg_record, sort_keys=True, separators=(",", ":"))
    assert prereg_record["phase"] == "before_holdout_payload_read"
    assert "solver_view" not in prereg_encoded
    assert "answer" not in prereg_encoded
    with pytest.raises(ValueError, match="secrets differ"):
        preregistration.manifest_material(StudySecrets.generate())

    prepared = open_preregistered_holdout_study(
        preregistration,
        holdout_payload_json=payload_path.read_text(encoding="utf-8"),
        secrets_material=secrets_material,
    )
    assert len(prepared.slots) == 20
    assert len(prepared.commitments) == 20
    assert len(prepared.retrieval_audits) == 20
    assert {
        (slot.pair_id, slot.condition) for slot in prepared.slots
    } == {(pair_id, condition) for pair_id in range(10) for condition in Condition}
    assert all(
        slot.dispatch.run_spec_digest == prepared.plan.spec.digest
        for slot in prepared.slots
    )
    record = prepared.private_precommitment_record()
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
    assert "numbered_solution" not in encoded
    assert "H01" not in encoded
    assert "brain_on" in encoded and "brain_off" in encoded
    assert prepared.plan.config.weights.localization == 0.0
    assert prepared.plan.config.runtime.executable_provenance == (
        prepared.executable_provenance
    )
