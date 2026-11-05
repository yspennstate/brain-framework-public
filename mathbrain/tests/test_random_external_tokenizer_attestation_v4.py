from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace

import pytest

from math_brain.random_external_tokenizer_attestation_v4 import (
    PacketPaddingInputV4,
    PacketPaddingPolicyV4,
    RandomExternalPacketPaddingInfeasible,
    RandomExternalTokenizerError,
    cold_replay_random_external_tokenizer_attestation_v4,
    materialize_random_external_tokenizer_attestation_v4,
)
from math_brain.three_arm_tokenizer_attestation_v3 import (
    OfflineTokenizerBackendV3,
    TokenizerBackendPairV3,
    TokenizerEvidenceClassV3,
    TokenizerSpecV3,
    TokenizerSuiteManifestV3,
    bind_tokenizer_suite_v3,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _byte_primary(text: str) -> list[int]:
    return list(text.encode("utf-8"))


def _byte_verifier(text: str) -> tuple[int, ...]:
    return tuple(text.encode("utf-8"))


def proxy_suite():
    spec = TokenizerSpecV3(
        tokenizer_id="byte-v4-test",
        encoding_name="byte-v4-test-encoding",
        vocabulary_artifact_digest=_digest("byte-v4-vocabulary"),
        encode_options_digest=_digest("ordinary-no-specials"),
        primary_implementation_id="byte-v4-primary",
        primary_implementation_artifact_digest=_digest("byte-v4-primary-artifact"),
        verifier_implementation_id="byte-v4-verifier",
        verifier_implementation_artifact_digest=_digest("byte-v4-verifier-artifact"),
    )
    pair = TokenizerBackendPairV3(
        primary=OfflineTokenizerBackendV3(
            tokenizer_id=spec.tokenizer_id,
            implementation_id=spec.primary_implementation_id,
            implementation_artifact_digest=spec.primary_implementation_artifact_digest,
            vocabulary_artifact_digest=spec.vocabulary_artifact_digest,
            encode_options_digest=spec.encode_options_digest,
            encoder=_byte_primary,
        ),
        verifier=OfflineTokenizerBackendV3(
            tokenizer_id=spec.tokenizer_id,
            implementation_id=spec.verifier_implementation_id,
            implementation_artifact_digest=spec.verifier_implementation_artifact_digest,
            vocabulary_artifact_digest=spec.vocabulary_artifact_digest,
            encode_options_digest=spec.encode_options_digest,
            encoder=_byte_verifier,
        ),
    )
    manifest = TokenizerSuiteManifestV3(
        requested_model_selector="desktop-app-requested-model",
        evidence_class=TokenizerEvidenceClassV3.ENGINEERING_PROXY,
        tokenizers=(spec,),
        engineering_proxy_basis_digest=_digest("offline-proxy-not-served-identity"),
    )
    return bind_tokenizer_suite_v3(
        manifest,
        (pair,),
        expected_manifest_digest=manifest.digest,
    )


def policy(*, minimum: int = 0, maximum: int = 32) -> PacketPaddingPolicyV4:
    return PacketPaddingPolicyV4(
        base_format_template_digest=_digest("base-two-line-packet-format"),
        minimum_padding_bytes=minimum,
        maximum_padding_bytes=maximum,
        padding_patterns=("\t", " "),
        maximum_candidates=(maximum - minimum + 1) * 2,
    )


def inputs() -> tuple[PacketPaddingInputV4, ...]:
    return (
        PacketPaddingInputV4(
            case_id="case-a",
            draft_digest=_digest("draft-a"),
            input_text="Title A\nShort guidance.",
            input_format_template_digest=_digest("base-two-line-packet-format"),
        ),
        PacketPaddingInputV4(
            case_id="case-b",
            draft_digest=_digest("draft-b"),
            input_text="Title B\nLonger guidance text.",
            input_format_template_digest=_digest("base-two-line-packet-format"),
        ),
    )


def test_materializes_one_canonical_exact_signature_with_proxy_limits() -> None:
    attestation = materialize_random_external_tokenizer_attestation_v4(
        inputs(),
        suite=proxy_suite(),
        policy=policy(),
    )

    assert len(attestation.receipts) == 2
    assert len({item.measurement.matching_signature for item in attestation.receipts}) == 1
    assert len({len(item.final_text.encode("utf-8")) for item in attestation.receipts}) == 1
    assert attestation.served_tokenizer_identity_verified is False
    assert attestation.served_model_identity_verified is False
    assert attestation.confirmatory_claim_authorized is False
    public = attestation.public_commitment()
    assert public["evidence_class"] == "engineering_proxy"
    assert public["served_tokenizer_identity_verified"] is False
    rendered = repr(public)
    assert "Title A" not in rendered
    assert "padding_digest" not in rendered


def test_cold_replay_rebuilds_from_raw_inputs_and_rejects_changed_text() -> None:
    suite = proxy_suite()
    padding_policy = policy()
    frozen = materialize_random_external_tokenizer_attestation_v4(
        inputs(),
        suite=suite,
        policy=padding_policy,
    )
    replayed = cold_replay_random_external_tokenizer_attestation_v4(
        inputs(),
        suite=suite,
        policy=padding_policy,
        expected_tokenizer_attestation_digest=frozen.content_digest,
    )
    assert replayed.content_digest == frozen.content_digest
    parameters = inspect.signature(cold_replay_random_external_tokenizer_attestation_v4).parameters
    assert "attestation" not in parameters
    assert not any("verified" in name for name in parameters)

    changed = list(inputs())
    changed[0] = replace(changed[0], input_text=changed[0].input_text + "x")
    with pytest.raises(RandomExternalTokenizerError, match="cold replay differs"):
        cold_replay_random_external_tokenizer_attestation_v4(
            changed,
            suite=suite,
            policy=padding_policy,
            expected_tokenizer_attestation_digest=frozen.content_digest,
        )


def test_zero_inputs_is_a_valid_exact_empty_attestation() -> None:
    attestation = materialize_random_external_tokenizer_attestation_v4(
        (),
        suite=proxy_suite(),
        policy=policy(),
    )

    assert attestation.receipts == ()
    assert attestation.common_signature_digest is None
    assert attestation.public_commitment()["input_count"] == 0


def test_finite_padding_search_can_fail_instead_of_relaxing_exactness() -> None:
    with pytest.raises(RandomExternalPacketPaddingInfeasible, match="no common"):
        materialize_random_external_tokenizer_attestation_v4(
            inputs(),
            suite=proxy_suite(),
            policy=policy(minimum=0, maximum=0),
        )


def test_tampered_token_or_padding_receipt_is_rejected() -> None:
    attestation = materialize_random_external_tokenizer_attestation_v4(
        inputs(),
        suite=proxy_suite(),
        policy=policy(),
    )
    first = attestation.receipts[0]
    with pytest.raises(RandomExternalTokenizerError, match="final byte length"):
        replace(first, final_text=first.final_text + " ")


def test_packet_policy_forbids_newline_padding_that_changes_section_shape() -> None:
    with pytest.raises(RandomExternalTokenizerError, match="space and tab"):
        PacketPaddingPolicyV4(
            base_format_template_digest=_digest("base-two-line-packet-format"),
            minimum_padding_bytes=0,
            maximum_padding_bytes=4,
            padding_patterns=("\n",),
            maximum_candidates=5,
        )
