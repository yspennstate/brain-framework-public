from __future__ import annotations

import inspect

import pytest

import math_brain.random_external_pilot_evaluation_v4 as evaluation
import math_brain.random_external_scoring_authority_v4 as authority
from math_brain.canonical_ed25519_v3 import CanonicalEd25519SignerV3
from math_brain.random_external_pilot_execution_v4 import (
    BoundRandomExternalExecutionV4,
    RandomExternalClosedDispositionV4,
    RandomExternalExecutionClosureV4,
)
from math_brain.random_external_scoring_authority_v4 import (
    RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SIGNING_DOMAIN_V4,
    AuthenticatedRandomExternalScorerV4,
    AuthenticatedRandomExternalScoreV4,
    AuthenticatedRandomScoreVerifierV4,
    ClosedAttemptDispositionV4,
    RandomExternalLedgerScoreEvidenceV4,
    RandomExternalScoringPrecommitmentV4,
    ScoringAuthorizationReplayV4,
)


def test_authenticated_score_uses_the_exact_dedicated_domain_and_raw_seed() -> None:
    assert RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SIGNING_DOMAIN_V4 == (
        "org.math-research-brain/random-external-authenticated-score/v4"
    )
    scorer = AuthenticatedRandomExternalScorerV4(b"A" * 32)
    direct = CanonicalEd25519SignerV3(
        b"A" * 32,
        domain="org.math-research-brain/random-external-authenticated-score/v4",
    )

    assert scorer.commitment == direct.authority_commitment
    assert type(scorer.verifier()) is AuthenticatedRandomScoreVerifierV4


def test_production_scorer_has_no_caller_outcome_or_runtime_flag_surface() -> None:
    parameters = inspect.signature(AuthenticatedRandomExternalScorerV4.score).parameters
    forbidden = {
        "expected_answer",
        "model_attestation",
        "model_identity_verified",
        "outcome",
        "response_text",
        "runner_verifier",
        "runtime_eligible",
        "started_once_verified",
    }

    assert forbidden.isdisjoint(parameters)
    source = inspect.getsource(authority.AuthenticatedRandomExternalScorerV4.score)
    assert "RandomExternalScoringReceiptSignerV4" not in source
    assert "RandomExternalAttemptRecordV4" not in source


def test_pre_run_certificate_exposes_strict_pre_started_authorization_replay() -> None:
    parameters = inspect.signature(
        AuthenticatedRandomExternalScorerV4.seal_precommitment,
        eval_str=True,
    ).parameters

    assert parameters["bound"].annotation is BoundRandomExternalExecutionV4
    assert "repository_root" in parameters
    fields = inspect.signature(RandomExternalScoringPrecommitmentV4).parameters
    assert "execution_implementation_manifest_digest" in fields
    assert "execution_authorization_digest" not in fields
    replay_fields = inspect.signature(ScoringAuthorizationReplayV4).parameters
    assert "scoring_precommitment_digest" in replay_fields
    replay_source = inspect.getsource(
        AuthenticatedRandomScoreVerifierV4.replay_for_execution_authorization
    )
    assert "must precede every STARTED record" in replay_source


def test_post_run_verifier_requires_transitive_execution_authorization_binding() -> None:
    source = inspect.getsource(
        AuthenticatedRandomScoreVerifierV4.verify_acknowledged_precommitment
    )

    assert '"scoring_precommitment_digest"' in source
    assert "execution authorization did not bind" in source


def test_scoring_consumes_only_execution_owned_locked_closure() -> None:
    fields = inspect.signature(RandomExternalLedgerScoreEvidenceV4, eval_str=True).parameters
    assert tuple(fields) == (
        "acknowledged",
        "closure",
        "expected_closure_digest",
        "repository_root",
        "ordinal",
    )
    assert fields["closure"].annotation is RandomExternalExecutionClosureV4
    assert ClosedAttemptDispositionV4 is RandomExternalClosedDispositionV4

    source = inspect.getsource(authority._derive_attempt_evidence_v4)
    assert "closed_artifacts_for_random_external_scoring_v4" in source
    assert "BURNED_RUNNER_FAILURE" in source
    assert "RandomExternalClosedAttemptManifestV4" not in authority.__all__
    assert "build_random_external_closed_attempt_manifest_v4" not in authority.__all__


def test_evaluator_exports_only_the_authenticated_score_boundary() -> None:
    exported = set(evaluation.__all__)

    assert "AuthenticatedRandomExternalScoreV4" in exported
    assert "AuthenticatedRandomScoreVerifierV4" in exported
    assert "RandomExternalScoredAttemptV4" not in exported
    assert "RandomExternalScoringReceiptSignerV4" not in exported
    assert "RandomExternalScoringReceiptVerifierV4" not in exported


def test_authenticated_score_type_hard_codes_model_identity_false() -> None:
    annotations = inspect.signature(AuthenticatedRandomExternalScoreV4).parameters
    assert "model_identity_verified" in annotations
    source = inspect.getsource(AuthenticatedRandomExternalScoreV4.__post_init__)
    assert "self.model_identity_verified is not False" in source


def test_legacy_or_nominal_attempt_is_rejected_before_signature_work() -> None:
    verifier = AuthenticatedRandomScoreVerifierV4(
        CanonicalEd25519SignerV3(
            b"V" * 32,
            domain=RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SIGNING_DOMAIN_V4,
        )
        .verifier()
        .public_key_bytes()
    )

    with pytest.raises(ValueError, match="legacy or nominal"):
        verifier.verify(object(), object())  # type: ignore[arg-type]
