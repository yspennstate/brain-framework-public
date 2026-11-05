from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from math_brain.canonical_ed25519_v3 import CanonicalEd25519SignerV3
from math_brain.models import canonical_json
from math_brain.three_arm_tokenizer_attestation_v3 import (
    DEFAULT_PADDING_PATTERNS_V3,
    RAW_WHITESPACE_PADDING_SLOT_V3,
    SERVED_TOKENIZER_ATTESTATION_DOMAIN_V3,
    ExactPromptPairMaterializationV3,
    OfflineTokenizerBackendV3,
    PromptMaterializationSideV3,
    PromptPaddingInfeasibleError,
    PromptPaddingPolicyV3,
    ServedTokenizerAttestationV3,
    TokenizerAttestationError,
    TokenizerBackendPairV3,
    TokenizerEvidenceClassV3,
    TokenizerSpecV3,
    TokenizerSuiteManifestV3,
    TokenMeasurementScopeV3,
    bind_tokenizer_suite_v3,
    canonical_token_ids_digest_v3,
    materialize_exact_prompt_pair_v3,
    measure_exact_text_v3,
    require_strict_served_attested_v3,
    served_tokenizer_attestation_unsigned_record_v3,
    verify_exact_prompt_pair_v3,
    verify_exact_text_measurement_v3,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _byte_primary(text: str) -> list[int]:
    return list(text.encode("utf-8"))


def _byte_verifier(text: str) -> tuple[int, ...]:
    encoded = text.encode("utf-8")
    return tuple(int.from_bytes(encoded[index : index + 1], "big") for index in range(len(encoded)))


def _merge_primary(text: str) -> list[int]:
    encoded = text.encode("utf-8")
    result: list[int] = []
    index = 0
    while index < len(encoded):
        pair = encoded[index : index + 2]
        if pair in {b"aa", b"  "}:
            result.append(100_000 + int.from_bytes(pair, "big"))
            index += 2
        else:
            result.append(encoded[index])
            index += 1
    return result


def _merge_verifier(text: str) -> tuple[int, ...]:
    encoded = text.encode("utf-8")
    output: list[int] = []
    cursor = 0
    while cursor != len(encoded):
        if cursor + 1 < len(encoded) and (
            (encoded[cursor] == ord("a") and encoded[cursor + 1] == ord("a"))
            or (encoded[cursor] == ord(" ") and encoded[cursor + 1] == ord(" "))
        ):
            output.append(100_000 + ((encoded[cursor] << 8) | encoded[cursor + 1]))
            cursor += 2
            continue
        output.append(encoded[cursor])
        cursor += 1
    return tuple(output)


def _spec(tokenizer_id: str) -> TokenizerSpecV3:
    return TokenizerSpecV3(
        tokenizer_id=tokenizer_id,
        encoding_name=f"{tokenizer_id}-encoding",
        vocabulary_artifact_digest=_digest(f"{tokenizer_id}-vocabulary"),
        encode_options_digest=_digest(f"{tokenizer_id}-ordinary-no-specials"),
        primary_implementation_id=f"{tokenizer_id}-primary",
        primary_implementation_artifact_digest=_digest(f"{tokenizer_id}-primary-artifact"),
        verifier_implementation_id=f"{tokenizer_id}-verifier",
        verifier_implementation_artifact_digest=_digest(f"{tokenizer_id}-verifier-artifact"),
    )


def _pair(
    spec: TokenizerSpecV3,
    primary,
    verifier,
) -> TokenizerBackendPairV3:
    return TokenizerBackendPairV3(
        primary=OfflineTokenizerBackendV3(
            tokenizer_id=spec.tokenizer_id,
            implementation_id=spec.primary_implementation_id,
            implementation_artifact_digest=spec.primary_implementation_artifact_digest,
            vocabulary_artifact_digest=spec.vocabulary_artifact_digest,
            encode_options_digest=spec.encode_options_digest,
            encoder=primary,
        ),
        verifier=OfflineTokenizerBackendV3(
            tokenizer_id=spec.tokenizer_id,
            implementation_id=spec.verifier_implementation_id,
            implementation_artifact_digest=spec.verifier_implementation_artifact_digest,
            vocabulary_artifact_digest=spec.vocabulary_artifact_digest,
            encode_options_digest=spec.encode_options_digest,
            encoder=verifier,
        ),
    )


def _components() -> tuple[tuple[TokenizerSpecV3, ...], tuple[TokenizerBackendPairV3, ...]]:
    byte_spec = _spec("byte-v1")
    merge_spec = _spec("merge-v1")
    return (
        (byte_spec, merge_spec),
        (
            _pair(byte_spec, _byte_primary, _byte_verifier),
            _pair(merge_spec, _merge_primary, _merge_verifier),
        ),
    )


def _proxy_suite():
    specs, pairs = _components()
    manifest = TokenizerSuiteManifestV3(
        requested_model_selector="gpt-test-selector",
        evidence_class=TokenizerEvidenceClassV3.ENGINEERING_PROXY,
        tokenizers=specs,
        engineering_proxy_basis_digest=_digest("proxy-basis-no-served-mapping"),
    )
    return bind_tokenizer_suite_v3(
        manifest,
        tuple(reversed(pairs)),
        expected_manifest_digest=manifest.digest,
    )


def _byte_only_proxy_suite():
    specs, pairs = _components()
    manifest = TokenizerSuiteManifestV3(
        requested_model_selector="gpt-test-selector",
        evidence_class=TokenizerEvidenceClassV3.ENGINEERING_PROXY,
        tokenizers=(specs[0],),
        engineering_proxy_basis_digest=_digest("byte-only-proxy-basis"),
    )
    return bind_tokenizer_suite_v3(
        manifest,
        (pairs[0],),
        expected_manifest_digest=manifest.digest,
    )


def _served_suite():
    specs, pairs = _components()
    signer = CanonicalEd25519SignerV3(
        b"\x17" * 32,
        domain=SERVED_TOKENIZER_ATTESTATION_DOMAIN_V3,
    )
    manifest = TokenizerSuiteManifestV3(
        requested_model_selector="gpt-test-selector",
        evidence_class=TokenizerEvidenceClassV3.SERVED_ATTESTED,
        tokenizers=specs,
        served_attestation_authority_commitment=signer.authority_commitment,
    )
    unsigned = served_tokenizer_attestation_unsigned_record_v3(
        requested_model_selector=manifest.requested_model_selector,
        observed_served_model=manifest.requested_model_selector,
        tokenizer_spec_digests=tuple(sorted(spec.digest for spec in specs)),
        response_model_identity_evidence_digest=_digest("response-model-identity"),
        model_tokenizer_mapping_evidence_digest=_digest("model-tokenizer-mapping"),
        authority_commitment=signer.authority_commitment,
    )
    attestation = ServedTokenizerAttestationV3(
        requested_model_selector=manifest.requested_model_selector,
        observed_served_model=manifest.requested_model_selector,
        tokenizer_spec_digests=tuple(sorted(spec.digest for spec in specs)),
        response_model_identity_evidence_digest=_digest("response-model-identity"),
        model_tokenizer_mapping_evidence_digest=_digest("model-tokenizer-mapping"),
        authority_commitment=signer.authority_commitment,
        authority_signature=signer.sign(canonical_json(unsigned).encode("utf-8")),
    )
    return bind_tokenizer_suite_v3(
        manifest,
        pairs,
        expected_manifest_digest=manifest.digest,
        served_attestation=attestation,
        served_attestation_verifier=signer.verifier(),
    )


def _policy(*, minimum: int = 1, maximum: int = 16) -> PromptPaddingPolicyV3:
    return PromptPaddingPolicyV3(
        wrapper_digest=_digest("exact-final-solver-prompt-wrapper"),
        minimum_padding_bytes=minimum,
        maximum_padding_bytes=maximum,
        maximum_candidates=(maximum - minimum + 1) * len(DEFAULT_PADDING_PATTERNS_V3),
    )


def _template(body: str) -> str:
    return "MATH_SOLVER_PROTOCOL_V3\n" + body + RAW_WHITESPACE_PADDING_SLOT_V3


def test_proxy_status_is_explicit_and_strict_gate_fails_closed() -> None:
    suite = _proxy_suite()
    first = _template("task=one;support=A")
    second = _template("task=two;support=BBBB")
    policy = _policy()
    materialized = materialize_exact_prompt_pair_v3(
        first,
        second,
        suite,
        policy,
    )

    assert suite.strict_served_attested_eligible is False
    assert materialized.evidence_class is TokenizerEvidenceClassV3.ENGINEERING_PROXY
    with pytest.raises(TokenizerAttestationError, match="engineering_proxy only"):
        require_strict_served_attested_v3(
            materialized,
            suite,
            first_prompt_template=first,
            second_prompt_template=second,
            policy=policy,
            expected_public_commitment_digest=materialized.public_commitment_digest,
        )


def test_evidence_class_cannot_be_implicit_or_unlabeled() -> None:
    specs, _ = _components()
    with pytest.raises(TokenizerAttestationError, match="evidence class"):
        TokenizerSuiteManifestV3(
            requested_model_selector="gpt-test-selector",
            evidence_class="served_attested",  # type: ignore[arg-type]
            tokenizers=specs,
            served_attestation_authority_commitment=_digest("authority"),
        )
    with pytest.raises(TokenizerAttestationError, match="proxy-basis"):
        TokenizerSuiteManifestV3(
            requested_model_selector="gpt-test-selector",
            evidence_class=TokenizerEvidenceClassV3.ENGINEERING_PROXY,
            tokenizers=specs,
        )


def test_served_status_requires_signed_identity_mapping_and_exact_selector() -> None:
    suite = _served_suite()
    first = _template("task=one;support=A")
    second = _template("task=two;support=BBBB")
    policy = _policy()
    materialized = materialize_exact_prompt_pair_v3(
        first,
        second,
        suite,
        policy,
    )
    require_strict_served_attested_v3(
        materialized,
        suite,
        first_prompt_template=first,
        second_prompt_template=second,
        policy=policy,
        expected_public_commitment_digest=materialized.public_commitment_digest,
    )
    assert suite.strict_served_attested_eligible is True

    specs, pairs = _components()
    signer = CanonicalEd25519SignerV3(
        b"\x19" * 32,
        domain=SERVED_TOKENIZER_ATTESTATION_DOMAIN_V3,
    )
    manifest = TokenizerSuiteManifestV3(
        requested_model_selector="requested-model",
        evidence_class=TokenizerEvidenceClassV3.SERVED_ATTESTED,
        tokenizers=specs,
        served_attestation_authority_commitment=signer.authority_commitment,
    )
    unsigned = served_tokenizer_attestation_unsigned_record_v3(
        requested_model_selector="requested-model",
        observed_served_model="different-served-model",
        tokenizer_spec_digests=tuple(sorted(spec.digest for spec in specs)),
        response_model_identity_evidence_digest=_digest("response-identity"),
        model_tokenizer_mapping_evidence_digest=_digest("mapping"),
        authority_commitment=signer.authority_commitment,
    )
    wrong_model = ServedTokenizerAttestationV3(
        requested_model_selector="requested-model",
        observed_served_model="different-served-model",
        tokenizer_spec_digests=tuple(sorted(spec.digest for spec in specs)),
        response_model_identity_evidence_digest=_digest("response-identity"),
        model_tokenizer_mapping_evidence_digest=_digest("mapping"),
        authority_commitment=signer.authority_commitment,
        authority_signature=signer.sign(canonical_json(unsigned).encode("utf-8")),
    )
    with pytest.raises(TokenizerAttestationError, match="served model differs"):
        bind_tokenizer_suite_v3(
            manifest,
            pairs,
            expected_manifest_digest=manifest.digest,
            served_attestation=wrong_model,
            served_attestation_verifier=signer.verifier(),
        )


def test_served_status_rejects_missing_or_invalid_signature_evidence() -> None:
    specs, pairs = _components()
    signer = CanonicalEd25519SignerV3(
        b"\x23" * 32,
        domain=SERVED_TOKENIZER_ATTESTATION_DOMAIN_V3,
    )
    manifest = TokenizerSuiteManifestV3(
        requested_model_selector="gpt-test-selector",
        evidence_class=TokenizerEvidenceClassV3.SERVED_ATTESTED,
        tokenizers=specs,
        served_attestation_authority_commitment=signer.authority_commitment,
    )
    with pytest.raises(TokenizerAttestationError, match="signed evidence"):
        bind_tokenizer_suite_v3(
            manifest,
            pairs,
            expected_manifest_digest=manifest.digest,
        )

    unsigned = served_tokenizer_attestation_unsigned_record_v3(
        requested_model_selector=manifest.requested_model_selector,
        observed_served_model=manifest.requested_model_selector,
        tokenizer_spec_digests=tuple(sorted(spec.digest for spec in specs)),
        response_model_identity_evidence_digest=_digest("identity"),
        model_tokenizer_mapping_evidence_digest=_digest("mapping"),
        authority_commitment=signer.authority_commitment,
    )
    signature = signer.sign(canonical_json(unsigned).encode("utf-8"))
    changed_final = "0" if signature[-1] != "0" else "1"
    forged = ServedTokenizerAttestationV3(
        requested_model_selector=manifest.requested_model_selector,
        observed_served_model=manifest.requested_model_selector,
        tokenizer_spec_digests=tuple(sorted(spec.digest for spec in specs)),
        response_model_identity_evidence_digest=_digest("identity"),
        model_tokenizer_mapping_evidence_digest=_digest("mapping"),
        authority_commitment=signer.authority_commitment,
        authority_signature=signature[:-1] + changed_final,
    )
    with pytest.raises(ValueError, match="signature"):
        bind_tokenizer_suite_v3(
            manifest,
            pairs,
            expected_manifest_digest=manifest.digest,
            served_attestation=forged,
            served_attestation_verifier=signer.verifier(),
        )


def test_strict_suite_retains_and_revalidates_signed_evidence() -> None:
    suite = _served_suite()
    assert suite.served_attestation is not None
    forged = replace(
        suite.served_attestation,
        model_tokenizer_mapping_evidence_digest=_digest("post-bind-forgery"),
    )
    with pytest.raises(ValueError, match="signature"):
        replace(
            suite,
            served_attestation=forged,
            served_attestation_digest=forged.digest,
        )
    with pytest.raises(TokenizerAttestationError, match="missing served attestation"):
        replace(suite, served_attestation_authority_public_key=None)


def test_strict_dispatch_gate_replays_the_exact_nonce_bearing_final_prompt() -> None:
    suite = _served_suite()
    policy = _policy(maximum=24)
    first = _template("response_nonce=nonce_a17;task=one;support=A")
    second = _template("response_nonce=nonce_b29;task=two;support=BBBB")
    materialized = materialize_exact_prompt_pair_v3(first, second, suite, policy)

    assert "response_nonce=nonce_a17" in materialized.first.final_prompt
    with pytest.raises(TokenizerAttestationError, match="independently regenerated"):
        require_strict_served_attested_v3(
            materialized,
            suite,
            first_prompt_template=first.replace("nonce_a17", "nonce_tampered"),
            second_prompt_template=second,
            policy=policy,
            expected_public_commitment_digest=materialized.public_commitment_digest,
        )


def test_independent_backend_identity_and_artifacts_are_enforced() -> None:
    specs, pairs = _components()
    manifest = TokenizerSuiteManifestV3(
        requested_model_selector="gpt-test-selector",
        evidence_class=TokenizerEvidenceClassV3.ENGINEERING_PROXY,
        tokenizers=specs,
        engineering_proxy_basis_digest=_digest("proxy"),
    )
    altered_primary = replace(
        pairs[0].primary,
        implementation_artifact_digest=_digest("swapped-artifact"),
    )
    with pytest.raises(TokenizerAttestationError, match="identity differs"):
        bind_tokenizer_suite_v3(
            manifest,
            (replace(pairs[0], primary=altered_primary), pairs[1]),
            expected_manifest_digest=manifest.digest,
        )

    with pytest.raises(TokenizerAttestationError, match="reuse one encoder"):
        TokenizerBackendPairV3(
            primary=pairs[0].primary,
            verifier=replace(pairs[0].verifier, encoder=pairs[0].primary.encoder),
        )


def test_independent_token_tuple_disagreement_fails_without_a_receipt() -> None:
    specs, pairs = _components()

    def disagreeing_verifier(text: str) -> tuple[int, ...]:
        return (*_byte_verifier(text), 999)

    bad_pair = replace(
        pairs[0],
        verifier=replace(pairs[0].verifier, encoder=disagreeing_verifier),
    )
    manifest = TokenizerSuiteManifestV3(
        requested_model_selector="gpt-test-selector",
        evidence_class=TokenizerEvidenceClassV3.ENGINEERING_PROXY,
        tokenizers=specs,
        engineering_proxy_basis_digest=_digest("proxy"),
    )
    suite = bind_tokenizer_suite_v3(
        manifest,
        (bad_pair, pairs[1]),
        expected_manifest_digest=manifest.digest,
    )
    with pytest.raises(TokenizerAttestationError, match="different token ids"):
        measure_exact_text_v3(
            "exact text",
            suite,
            scope=TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT,
            framing_digest=_digest("frame"),
        )


@pytest.mark.parametrize(
    "text",
    (
        "integer 17",
        "TeX: \\frac{7}{13}",
        "emoji: π☃️",
        "CRLF\r\nLF\nTAB\tend",
        'quotes: "x" and slash \\\\',
    ),
)
def test_exact_measurement_recomputes_unicode_and_control_bytes(text: str) -> None:
    suite = _proxy_suite()
    measurement = measure_exact_text_v3(
        text,
        suite,
        scope=TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT,
        framing_digest=_digest("frame"),
    )
    verify_exact_text_measurement_v3(text, measurement, suite)
    assert measurement.byte_length == len(text.encode("utf-8"))
    assert len(measurement.tokenizer_receipts) == 2


def test_token_id_digest_has_unambiguous_u64_framing() -> None:
    kwargs = {
        "tokenizer_spec_digest": _digest("spec"),
        "text_digest": _digest("text"),
        "scope": TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT,
        "framing_digest": _digest("frame"),
    }
    first = canonical_token_ids_digest_v3([1, 23], **kwargs)
    second = canonical_token_ids_digest_v3([12, 3], **kwargs)
    assert first != second
    assert first == canonical_token_ids_digest_v3((1, 23), **kwargs)
    with pytest.raises(TokenizerAttestationError, match="unsigned 64-bit"):
        canonical_token_ids_digest_v3([True], **kwargs)
    with pytest.raises(TokenizerAttestationError, match="unsigned 64-bit"):
        canonical_token_ids_digest_v3([-1], **kwargs)


def test_forged_count_digest_text_and_framing_all_fail_recomputation() -> None:
    suite = _proxy_suite()
    measurement = measure_exact_text_v3(
        "sealed final prompt",
        suite,
        scope=TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT,
        framing_digest=_digest("frame"),
    )
    forged_receipt = replace(
        measurement.tokenizer_receipts[0],
        token_count=measurement.tokenizer_receipts[0].token_count + 1,
    )
    forged = replace(
        measurement,
        tokenizer_receipts=(forged_receipt, *measurement.tokenizer_receipts[1:]),
    )
    with pytest.raises(TokenizerAttestationError, match="regenerated evidence"):
        verify_exact_text_measurement_v3("sealed final prompt", forged, suite)
    with pytest.raises(TokenizerAttestationError, match="regenerated evidence"):
        verify_exact_text_measurement_v3("changed final prompt", measurement, suite)
    reframed = replace(measurement, framing_digest=_digest("other-frame"))
    with pytest.raises(TokenizerAttestationError, match="regenerated evidence"):
        verify_exact_text_measurement_v3("sealed final prompt", reframed, suite)


def test_padding_matches_exact_final_prompt_bytes_and_every_tokenizer() -> None:
    suite = _proxy_suite()
    policy = _policy(maximum=20)
    first = _template("task_id=solvertaskv3_aaaaaaaa;support=a")
    second = _template("task_id=solvertaskv3_bbbbbbbb;support=b")
    materialized = materialize_exact_prompt_pair_v3(first, second, suite, policy)

    assert materialized.first.measurement.matching_signature == (
        materialized.second.measurement.matching_signature
    )
    assert len(materialized.first.final_prompt.encode("utf-8")) == len(
        materialized.second.final_prompt.encode("utf-8")
    )
    assert materialized.first.padding
    assert materialized.second.padding
    assert set(materialized.first.padding + materialized.second.padding) <= {" ", "\t", "\n"}
    assert materialized.first.measurement.scope is TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT
    verify_exact_prompt_pair_v3(
        materialized,
        first,
        second,
        suite,
        policy,
        expected_public_commitment_digest=materialized.public_commitment_digest,
    )


def test_standalone_equal_counts_do_not_substitute_for_final_prompt_measurement() -> None:
    suite = _proxy_suite()
    frame = _digest("standalone-frame")
    left_standalone = measure_exact_text_v3(
        "a",
        suite,
        scope=TokenMeasurementScopeV3.PACKET_TEXT,
        framing_digest=frame,
    )
    right_standalone = measure_exact_text_v3(
        "b",
        suite,
        scope=TokenMeasurementScopeV3.PACKET_TEXT,
        framing_digest=frame,
    )
    assert left_standalone.matching_signature == right_standalone.matching_signature

    left_unpadded = "prefix-a" + "a"
    right_unpadded = "prefix-a" + "b"
    left_full = measure_exact_text_v3(
        left_unpadded,
        suite,
        scope=TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT,
        framing_digest=_digest("wrapper"),
    )
    right_full = measure_exact_text_v3(
        right_unpadded,
        suite,
        scope=TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT,
        framing_digest=_digest("wrapper"),
    )
    assert left_full.matching_signature != right_full.matching_signature

    policy = PromptPaddingPolicyV3(
        wrapper_digest=_digest("wrapper"),
        minimum_padding_bytes=1,
        maximum_padding_bytes=16,
        maximum_candidates=16 * len(DEFAULT_PADDING_PATTERNS_V3),
    )
    materialized = materialize_exact_prompt_pair_v3(
        left_unpadded + RAW_WHITESPACE_PADDING_SLOT_V3,
        right_unpadded + RAW_WHITESPACE_PADDING_SLOT_V3,
        suite,
        policy,
    )
    assert materialized.first.measurement.matching_signature == (
        materialized.second.measurement.matching_signature
    )


def test_padding_search_is_deterministic_and_public_commitment_is_private() -> None:
    suite = _proxy_suite()
    policy = _policy(maximum=20)
    sensitive_packet_text = "PRIVATE_PACKET_BODY_9217"
    first = _template(f"problem=one;support={sensitive_packet_text}")
    second = _template("problem=one;support=irrelevant-method")
    one = materialize_exact_prompt_pair_v3(first, second, suite, policy)
    two = materialize_exact_prompt_pair_v3(first, second, suite, policy)

    assert one == two
    public = canonical_json(one.public_commitment())
    assert sensitive_packet_text not in public
    assert sensitive_packet_text not in repr(one)
    assert sensitive_packet_text not in canonical_json(one.private_commitment_record())
    assert "padding_byte_length" not in public
    assert "tokenizer_receipts" not in public
    assert "final_prompt_digest" not in public
    assert "packet_id" not in public
    assert "C:\\" not in public


def test_valid_but_noncanonical_padding_is_rejected_by_replay() -> None:
    suite = _byte_only_proxy_suite()
    policy = _policy(maximum=24)
    first_template = _template("task=one;support=A")
    second_template = _template("task=two;support=BBBB")
    canonical = materialize_exact_prompt_pair_v3(
        first_template,
        second_template,
        suite,
        policy,
    )
    first_padding = canonical.first.padding + "\t"
    second_padding = canonical.second.padding + "\t"
    first_base = canonical.first.final_prompt[: -len(canonical.first.padding)]
    second_base = canonical.second.final_prompt[: -len(canonical.second.padding)]
    first_final = first_base + first_padding
    second_final = second_base + second_padding
    first_measurement = measure_exact_text_v3(
        first_final,
        suite,
        scope=TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT,
        framing_digest=policy.wrapper_digest,
    )
    second_measurement = measure_exact_text_v3(
        second_final,
        suite,
        scope=TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT,
        framing_digest=policy.wrapper_digest,
    )
    assert first_measurement.matching_signature == second_measurement.matching_signature

    def side(base: str, padding: str, final: str, measurement):
        return PromptMaterializationSideV3(
            final_prompt=final,
            padding=padding,
            base_prompt_digest=_digest_bytes(base.encode("utf-8")),
            final_prompt_digest=_digest_bytes(final.encode("utf-8")),
            padding_digest=_digest_bytes(padding.encode("ascii")),
            padding_byte_length=len(padding.encode("ascii")),
            measurement=measurement,
        )

    signature_digest = _signature_digest(first_measurement)
    alternative = ExactPromptPairMaterializationV3(
        first=side(first_base, first_padding, first_final, first_measurement),
        second=side(second_base, second_padding, second_final, second_measurement),
        suite_manifest_digest=suite.manifest.digest,
        suite_binding_digest=suite.binding_digest,
        padding_policy_digest=policy.digest,
        evidence_class=suite.manifest.evidence_class,
        common_signature_digest=signature_digest,
    )
    with pytest.raises(TokenizerAttestationError, match="independently regenerated"):
        verify_exact_prompt_pair_v3(
            alternative,
            first_template,
            second_template,
            suite,
            policy,
            expected_public_commitment_digest=alternative.public_commitment_digest,
        )


def _digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _signature_digest(measurement) -> str:
    return _digest_bytes(
        canonical_json(
            {
                "byte_length": measurement.byte_length,
                "tokenizer_counts": [
                    {
                        "token_count": receipt.token_count,
                        "tokenizer_artifact_digest": receipt.tokenizer_artifact_digest,
                        "tokenizer_id": receipt.tokenizer_id,
                    }
                    for receipt in measurement.tokenizer_receipts
                ],
            }
        ).encode("utf-8")
    )


def test_padding_policy_is_finite_and_infeasible_means_failure() -> None:
    suite = _proxy_suite()
    policy = _policy(minimum=1, maximum=1)
    with pytest.raises(PromptPaddingInfeasibleError, match="no byte-and-token exact solution"):
        materialize_exact_prompt_pair_v3(
            _template("short"),
            _template("much-longer"),
            suite,
            policy,
        )
    with pytest.raises(TokenizerAttestationError, match="candidate universe"):
        PromptPaddingPolicyV3(
            wrapper_digest=_digest("wrapper"),
            minimum_padding_bytes=1,
            maximum_padding_bytes=100,
            maximum_candidates=1,
        )


def test_padding_slot_and_raw_whitespace_policy_fail_closed() -> None:
    suite = _proxy_suite()
    policy = _policy()
    with pytest.raises(TokenizerAttestationError, match="must end"):
        materialize_exact_prompt_pair_v3(
            RAW_WHITESPACE_PADDING_SLOT_V3 + "trailing-data",
            _template("valid"),
            suite,
            policy,
        )
    with pytest.raises(TokenizerAttestationError, match="raw ASCII"):
        PromptPaddingPolicyV3(
            wrapper_digest=_digest("wrapper"),
            minimum_padding_bytes=1,
            maximum_padding_bytes=1,
            padding_patterns=("x",),
            maximum_candidates=1,
        )


def test_wrong_public_commitment_and_prompt_tamper_fail() -> None:
    suite = _proxy_suite()
    policy = _policy()
    first = _template("task=one;support=A")
    second = _template("task=two;support=BBBB")
    materialized = materialize_exact_prompt_pair_v3(first, second, suite, policy)
    with pytest.raises(TokenizerAttestationError, match="public commitment"):
        verify_exact_prompt_pair_v3(
            materialized,
            first,
            second,
            suite,
            policy,
            expected_public_commitment_digest=_digest("wrong-root"),
        )
    with pytest.raises(TokenizerAttestationError, match="independently regenerated"):
        verify_exact_prompt_pair_v3(
            materialized,
            first.replace("support=A", "support=Z"),
            second,
            suite,
            policy,
            expected_public_commitment_digest=materialized.public_commitment_digest,
        )


def test_backend_token_ids_reject_bool_negative_and_unbounded_values() -> None:
    spec = _spec("bad-v1")

    def invalid(_text: str):
        return [True]

    backend = _pair(spec, invalid, _byte_verifier).primary
    with pytest.raises(TokenizerAttestationError, match="unsigned 64-bit"):
        backend.encode_ordinary("x")
    for value in (-1, 2**64):

        def invalid_range(_text: str, value=value):
            return [value]

        changed = replace(backend, encoder=invalid_range)
        with pytest.raises(TokenizerAttestationError, match="unsigned 64-bit"):
            changed.encode_ordinary("x")


def test_public_json_never_serializes_callable_or_token_vectors() -> None:
    suite = _proxy_suite()
    materialized = materialize_exact_prompt_pair_v3(
        _template("task=one;support=private-one"),
        _template("task=two;support=private-two"),
        suite,
        _policy(),
    )
    payload = json.loads(canonical_json(materialized.public_commitment()))
    assert set(payload) == {
        "evidence_class",
        "materialization_digest",
        "padding_policy_digest",
        "record_type",
        "schema_version",
        "suite_manifest_digest",
    }
