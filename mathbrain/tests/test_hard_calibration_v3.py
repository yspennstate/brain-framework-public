from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

import math_brain.hard_calibration_v3 as hard_calibration
from math_brain.hard_calibration_v3 import (
    bind_hard_calibration_outcomes_v3,
    build_hard_calibration_preregistration_v3,
)
from math_brain.hard_challenge_v2 import (
    CALIBRATION_CASE_COUNT_V2,
    generate_public_calibration_bank_v2,
)

SEED = "hard-v2-calibration-v3-test-seed"
CASE_ID_KEY = bytes.fromhex("10" * 32)


@pytest.fixture(scope="module")
def preregistration():
    return build_hard_calibration_preregistration_v3(SEED, case_id_key=CASE_ID_KEY)


def test_fixed_hard_bank_builds_one_complete_v3_preregistration(preregistration) -> None:
    plan = preregistration.plan
    corpus = preregistration.corpus

    assert len(corpus.candidates) == CALIBRATION_CASE_COUNT_V2 == 320
    assert plan.axis_counts.to_dict() == {
        "domain": 4,
        "family": 4,
        "fault": 2,
        "generator_cell": 2,
        "layout": 1,
        "template": 4,
    }
    assert plan.eligible_arities == (8,)
    assert len(preregistration.verifier_identities) == 8
    assert all(
        len(candidate.executed_verifier_implementation_digests) == 2
        for candidate in corpus.candidates
    )
    assert all(
        candidate.verifier_identities == preregistration.verifier_identities
        for candidate in corpus.candidates
    )
    corpus.validate_against(plan)


def test_solver_views_hide_oracle_coordinates_and_direct_seed_ids(preregistration) -> None:
    for candidate in preregistration.corpus.candidates:
        payload = candidate.solver_view_payload()
        rendered = repr(payload).lower()
        assert "verifier" not in rendered
        assert "fault_line" not in payload
        assert "fault_label" not in payload
        assert candidate.stable_case_id.startswith("hard-v3-")
        assert "dv2-" not in candidate.stable_case_id.lower()


def test_public_commitment_view_omits_rank_manifest_seed_and_private_key(
    preregistration,
) -> None:
    payload = preregistration.public_commitment_payload()
    rendered = repr(payload)
    assert payload["case_id_key_commitment"] == preregistration.case_id_key_commitment
    assert payload["manifest_digest"] == preregistration.corpus.manifest.digest()
    assert preregistration.public_commitment_digest().startswith("sha256:")
    for forbidden in (
        "candidates",
        "case_id_key'",
        "fault_line",
        "fault_rank",
        "manifest':",
        "selection_seed",
        "stable_case_id",
    ):
        assert forbidden not in rendered


def test_preregistered_axes_are_balanced_and_complete(preregistration) -> None:
    candidates = preregistration.corpus.candidates
    assert Counter(candidate.domain_index for candidate in candidates) == Counter(
        {index: 80 for index in range(4)}
    )
    assert Counter(candidate.family_index for candidate in candidates) == Counter(
        {index: 80 for index in range(4)}
    )
    assert Counter(candidate.template_index for candidate in candidates) == Counter(
        {index: 80 for index in range(4)}
    )
    assert Counter(candidate.fault_index for candidate in candidates) == Counter({0: 160, 1: 160})
    assert Counter(candidate.generator_cell_index for candidate in candidates) == Counter(
        {0: 160, 1: 160}
    )
    assert Counter(candidate.fault_rank for candidate in candidates) == Counter(
        {0: 40, 1: 41, 2: 38, 3: 42, 4: 41, 5: 38, 6: 42, 7: 38}
    )


def test_build_is_deterministic(preregistration) -> None:
    repeated = build_hard_calibration_preregistration_v3(SEED, case_id_key=CASE_ID_KEY)
    assert repeated.plan == preregistration.plan
    assert repeated.corpus.commitment == preregistration.corpus.commitment
    assert repeated.corpus.manifest == preregistration.corpus.manifest


def test_case_ids_require_private_key_and_bind_its_commitment(preregistration) -> None:
    other = build_hard_calibration_preregistration_v3(
        SEED,
        case_id_key=bytes.fromhex("20" * 32),
    )
    first_ids = {candidate.stable_case_id for candidate in preregistration.corpus.candidates}
    other_ids = {candidate.stable_case_id for candidate in other.corpus.candidates}
    assert first_ids.isdisjoint(other_ids)
    assert other.case_id_key_commitment != preregistration.case_id_key_commitment
    assert other.plan.digest() != preregistration.plan.digest()

    with pytest.raises(ValueError, match="exactly 32 immutable bytes"):
        build_hard_calibration_preregistration_v3(SEED, case_id_key=b"short")


def test_wrapper_commitment_revalidates_its_nested_identity_map() -> None:
    preregistration = build_hard_calibration_preregistration_v3(
        SEED,
        case_id_key=CASE_ID_KEY,
    )
    object.__setattr__(preregistration, "verifier_identities", ())
    with pytest.raises(ValueError, match="identity map differs from generator"):
        _ = preregistration.commitment


def test_wrapper_rejects_valid_generic_corpus_with_answer_carrying_ids() -> None:
    preregistration = build_hard_calibration_preregistration_v3(
        SEED,
        case_id_key=CASE_ID_KEY,
    )
    forged_candidates = tuple(
        replace(
            candidate,
            stable_case_id=f"line-{candidate.fault_line}-{candidate.stable_case_id}",
        )
        for candidate in preregistration.corpus.candidates
    )
    forged_corpus = hard_calibration.build_preregistered_corpus(
        preregistration.plan,
        preregistration.corpus.manifest.seed,
        forged_candidates,
    )
    with pytest.raises(ValueError, match="keyed generator reconstruction"):
        hard_calibration.HardCalibrationPreregistrationV3(
            preregistration.plan,
            forged_corpus,
            preregistration.verifier_identities,
            preregistration.case_id_key_commitment,
            preregistration.case_id_key,
        )


def test_publication_commitment_rejects_stale_evaluator_source(
    preregistration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        hard_calibration,
        "_evaluator_protocol_digest",
        lambda: "sha256:" + "0" * 64,
    )
    with pytest.raises(ValueError, match="plan differs from current sealed generator"):
        _ = preregistration.commitment


def test_outcomes_bind_only_under_exact_opaque_case_universe(preregistration) -> None:
    outcomes = {
        candidate.stable_case_id: candidate.generator_counter % 2 == 0
        for candidate in preregistration.corpus.candidates
    }
    binding = bind_hard_calibration_outcomes_v3(preregistration, outcomes)
    assert len(binding.case_links) == CALIBRATION_CASE_COUNT_V2

    with pytest.raises(ValueError, match="cover the preregistered corpus exactly"):
        bind_hard_calibration_outcomes_v3(
            preregistration,
            dict(list(outcomes.items())[1:]),
        )

    cases_by_id = {
        link.stable_case_id: outcomes[link.stable_case_id] for link in binding.case_links
    }
    assert cases_by_id == outcomes


def test_outcome_mapping_rejects_non_boolean_values(preregistration) -> None:
    outcomes = {candidate.stable_case_id: False for candidate in preregistration.corpus.candidates}
    first = next(iter(outcomes))
    outcomes[first] = 0
    with pytest.raises(ValueError, match="exact string-to-boolean"):
        bind_hard_calibration_outcomes_v3(preregistration, outcomes)


def test_outcome_binding_replays_current_case_math_after_preregistration(
    preregistration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bank = list(generate_public_calibration_bank_v2())
    counter = preregistration.corpus.candidates[0].generator_counter
    object.__setattr__(bank[counter], "prompt", bank[counter].prompt + " altered")
    monkeypatch.setattr(
        hard_calibration,
        "generate_public_calibration_bank_v2",
        lambda: tuple(bank),
    )
    outcomes = {candidate.stable_case_id: False for candidate in preregistration.corpus.candidates}
    with pytest.raises(ValueError, match="exact mathematical certification"):
        bind_hard_calibration_outcomes_v3(preregistration, outcomes)


def test_outcome_binding_rejects_live_generic_binder_replacement(
    preregistration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes = {candidate.stable_case_id: False for candidate in preregistration.corpus.candidates}
    monkeypatch.setattr(
        hard_calibration.protocol_module,
        "bind_calibration_outcomes",
        lambda *_args, **_kwargs: "forged-result",
    )
    with pytest.raises(ValueError, match="protocol callback changed after import"):
        bind_hard_calibration_outcomes_v3(preregistration, outcomes)
