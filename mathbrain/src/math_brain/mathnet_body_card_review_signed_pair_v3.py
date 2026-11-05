"""Canonical portable envelope for the two independently signed body/card reviews.

The exact submission records are verified against reviewer keys committed by the
externally anchored preregistration before process-local submission objects are
reconstructed.  This is the cold verification boundary: it needs no ledger,
private keys, authentication file, runtime directory, or live Codex artifact.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import re

from .canonical_ed25519_v3 import CanonicalEd25519VerifierV3
from .mathnet_body_card_review_v3 import (
    BODY_CARD_REVIEW_VERSION_V3,
    BodyCardReviewerSlotV3,
    BodyCardSelectionReportV3,
    FrozenBodyCardEventFamilyBindingsV3,
    SignedBodyCardReviewManifestV3,
    _ascii_json,
    _exact_keys,
    _mapping,
    _mint_verified_body_card_submission_record_v3,
    _strict_json_text,
    body_card_reviewer_signing_domain_v3,
    parse_body_card_review_public_precommitment_v3,
    validate_body_card_reviewer_pair_v3,
    verify_and_select_body_card_reviews_v3,
    verify_signed_body_card_review_manifest_v3,
)

BODY_CARD_SIGNED_PAIR_SCHEMA_V3 = "org.math-research-brain/engineering-pilot-body-card-signed-pair"
BODY_CARD_SIGNED_PAIR_MAX_BYTES_V3 = 5_000_000
BODY_CARD_SIGNED_RECORD_MAX_BYTES_V3 = 1_800_000

_SIGNATURE_RE = re.compile(r"ed25519:[0-9a-f]{128}\Z")
_PUBLIC_KEY_RE = re.compile(r"[0-9a-f]{64}\Z")


def _manifest_payload(manifest: SignedBodyCardReviewManifestV3) -> dict[str, str]:
    if type(manifest) is not SignedBodyCardReviewManifestV3:
        raise ValueError("signed body/card pair requires exact manifests")
    manifest.__post_init__()
    record = manifest.submission.canonical_record()
    if not 0 < len(record) <= BODY_CARD_SIGNED_RECORD_MAX_BYTES_V3:
        raise ValueError("signed body/card submission record exceeds its byte budget")
    return {
        "authority_commitment": manifest.authority_commitment,
        "key_fingerprint": manifest.key_fingerprint,
        "signature": manifest.signature,
        "submission_record_base64": base64.b64encode(record).decode("ascii"),
    }


def serialize_signed_body_card_review_pair_v3(
    reviewer_a_manifest: SignedBodyCardReviewManifestV3,
    reviewer_b_manifest: SignedBodyCardReviewManifestV3,
    *,
    bindings: FrozenBodyCardEventFamilyBindingsV3,
    review_precommitment_bytes: bytes,
    expected_review_precommitment_digest: str,
) -> bytes:
    """Serialize the exact A/B signed records as one canonical LF-framed artifact."""

    if (
        type(reviewer_a_manifest) is not SignedBodyCardReviewManifestV3
        or type(reviewer_b_manifest) is not SignedBodyCardReviewManifestV3
    ):
        raise ValueError("signed body/card pair requires two exact manifests")
    if (
        reviewer_a_manifest.submission.reviewer_slot is not BodyCardReviewerSlotV3.REVIEWER_A
        or reviewer_b_manifest.submission.reviewer_slot is not BodyCardReviewerSlotV3.REVIEWER_B
    ):
        raise ValueError("signed body/card pair must be serialized in fixed A/B order")
    final_precommitment = parse_body_card_review_public_precommitment_v3(
        review_precommitment_bytes,
        expected_digest=expected_review_precommitment_digest,
    )
    verifier_a = _committed_verifier(
        final_precommitment,
        BodyCardReviewerSlotV3.REVIEWER_A,
    )
    verifier_b = _committed_verifier(
        final_precommitment,
        BodyCardReviewerSlotV3.REVIEWER_B,
    )
    verify_signed_body_card_review_manifest_v3(
        reviewer_a_manifest,
        verifier_a,
        expected_slot=BodyCardReviewerSlotV3.REVIEWER_A,
    )
    verify_signed_body_card_review_manifest_v3(
        reviewer_b_manifest,
        verifier_b,
        expected_slot=BodyCardReviewerSlotV3.REVIEWER_B,
    )
    validate_body_card_reviewer_pair_v3(
        reviewer_a_manifest.submission,
        reviewer_b_manifest.submission,
        reviewer_a_key_fingerprint=reviewer_a_manifest.key_fingerprint,
        reviewer_b_key_fingerprint=reviewer_b_manifest.key_fingerprint,
    )
    verify_and_select_body_card_reviews_v3(
        reviewer_a_manifest,
        reviewer_b_manifest,
        bindings=bindings,
        review_precommitment_bytes=review_precommitment_bytes,
        expected_review_precommitment_digest=expected_review_precommitment_digest,
    )
    encoded = (
        _ascii_json(
            {
                "reviewer_a": _manifest_payload(reviewer_a_manifest),
                "reviewer_b": _manifest_payload(reviewer_b_manifest),
                "schema": BODY_CARD_SIGNED_PAIR_SCHEMA_V3,
                "schema_version": BODY_CARD_REVIEW_VERSION_V3,
            }
        ).encode("ascii")
        + b"\n"
    )
    if len(encoded) > BODY_CARD_SIGNED_PAIR_MAX_BYTES_V3:
        raise ValueError("signed body/card pair exceeds its byte budget")
    return encoded


def _committed_verifier(
    final_precommitment: dict[str, object],
    slot: BodyCardReviewerSlotV3,
) -> CanonicalEd25519VerifierV3:
    core = _mapping(final_precommitment.get("core"), "anchored body/card core")
    label = "reviewer_a" if slot is BodyCardReviewerSlotV3.REVIEWER_A else "reviewer_b"
    committed = _mapping(core.get(label), f"anchored {label}")
    _exact_keys(
        committed,
        {
            "authority_commitment",
            "card_order_digest",
            "item_order_digest",
            "key_fingerprint",
            "output_schema_digest",
            "presentation_digest",
            "prompt_digest",
            "public_key_hex",
            "signing_domain",
        },
        f"anchored {label}",
    )
    public_key_hex = committed["public_key_hex"]
    if type(public_key_hex) is not str or _PUBLIC_KEY_RE.fullmatch(public_key_hex) is None:
        raise ValueError(f"anchored {label} public key is invalid")
    verifier = CanonicalEd25519VerifierV3(
        bytes.fromhex(public_key_hex),
        domain=body_card_reviewer_signing_domain_v3(slot),
    )
    if (
        committed["key_fingerprint"] != verifier.key_fingerprint
        or committed["authority_commitment"] != verifier.authority_commitment
        or committed["signing_domain"] != body_card_reviewer_signing_domain_v3(slot)
    ):
        raise ValueError(f"anchored {label} reviewer authority is inconsistent")
    return verifier


def _verify_then_reconstruct_manifest(
    raw_manifest: object,
    *,
    verifier: CanonicalEd25519VerifierV3,
    slot: BodyCardReviewerSlotV3,
) -> SignedBodyCardReviewManifestV3:
    label = "reviewer A" if slot is BodyCardReviewerSlotV3.REVIEWER_A else "reviewer B"
    value = _mapping(raw_manifest, f"portable {label} manifest")
    _exact_keys(
        value,
        {
            "authority_commitment",
            "key_fingerprint",
            "signature",
            "submission_record_base64",
        },
        f"portable {label} manifest",
    )
    if (
        value["authority_commitment"] != verifier.authority_commitment
        or value["key_fingerprint"] != verifier.key_fingerprint
    ):
        raise ValueError(f"portable {label} authority differs from the external anchor")
    signature = value["signature"]
    encoded_record = value["submission_record_base64"]
    if type(signature) is not str or _SIGNATURE_RE.fullmatch(signature) is None:
        raise ValueError(f"portable {label} signature is invalid")
    if type(encoded_record) is not str or not encoded_record:
        raise ValueError(f"portable {label} record is not canonical base64")
    try:
        record = base64.b64decode(encoded_record, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(f"portable {label} record is not canonical base64") from error
    if (
        len(record) > BODY_CARD_SIGNED_RECORD_MAX_BYTES_V3
        or base64.b64encode(record).decode("ascii") != encoded_record
    ):
        raise ValueError(f"portable {label} record framing is invalid")

    # Authority comes exclusively from the external anchor.  Verify the opaque
    # bytes before parsing any role, ledger, or preregistration fields they carry.
    verifier.verify(record, signature)
    submission = _mint_verified_body_card_submission_record_v3(record)
    if submission.reviewer_slot is not slot:
        raise ValueError(f"portable {label} signed record has the wrong role")
    return SignedBodyCardReviewManifestV3(
        submission=submission,
        key_fingerprint=verifier.key_fingerprint,
        authority_commitment=verifier.authority_commitment,
        signature=signature,
    )


def verify_and_select_signed_body_card_review_pair_bytes_v3(
    raw: bytes,
    *,
    bindings: FrozenBodyCardEventFamilyBindingsV3,
    review_precommitment_bytes: bytes,
    expected_review_precommitment_digest: str,
) -> BodyCardSelectionReportV3:
    """Cold-verify one pair using only portable bytes and the external anchor."""

    if (
        type(raw) is not bytes
        or not raw.endswith(b"\n")
        or len(raw) > BODY_CARD_SIGNED_PAIR_MAX_BYTES_V3
    ):
        raise ValueError("signed body/card pair framing is invalid")
    try:
        text = raw[:-1].decode("ascii", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("signed body/card pair is not canonical ASCII JSON") from error
    envelope = _mapping(
        _strict_json_text(
            text,
            "signed body/card pair",
            maximum=BODY_CARD_SIGNED_PAIR_MAX_BYTES_V3,
            ascii_canonical=True,
        ),
        "signed body/card pair",
    )
    _exact_keys(
        envelope,
        {"reviewer_a", "reviewer_b", "schema", "schema_version"},
        "signed body/card pair",
    )
    if (
        envelope["schema"] != BODY_CARD_SIGNED_PAIR_SCHEMA_V3
        or type(envelope["schema"]) is not str
        or envelope["schema_version"] != BODY_CARD_REVIEW_VERSION_V3
        or type(envelope["schema_version"]) is not int
    ):
        raise ValueError("signed body/card pair schema differs")
    final_precommitment = parse_body_card_review_public_precommitment_v3(
        review_precommitment_bytes,
        expected_digest=expected_review_precommitment_digest,
    )
    verifier_a = _committed_verifier(
        final_precommitment,
        BodyCardReviewerSlotV3.REVIEWER_A,
    )
    verifier_b = _committed_verifier(
        final_precommitment,
        BodyCardReviewerSlotV3.REVIEWER_B,
    )
    manifest_a = _verify_then_reconstruct_manifest(
        envelope["reviewer_a"],
        verifier=verifier_a,
        slot=BodyCardReviewerSlotV3.REVIEWER_A,
    )
    manifest_b = _verify_then_reconstruct_manifest(
        envelope["reviewer_b"],
        verifier=verifier_b,
        slot=BodyCardReviewerSlotV3.REVIEWER_B,
    )
    report = verify_and_select_body_card_reviews_v3(
        manifest_a,
        manifest_b,
        bindings=bindings,
        review_precommitment_bytes=review_precommitment_bytes,
        expected_review_precommitment_digest=expected_review_precommitment_digest,
    )
    if not hmac.compare_digest(
        serialize_signed_body_card_review_pair_v3(
            manifest_a,
            manifest_b,
            bindings=bindings,
            review_precommitment_bytes=review_precommitment_bytes,
            expected_review_precommitment_digest=expected_review_precommitment_digest,
        ),
        raw,
    ):
        raise ValueError("signed body/card pair changed during cold reconstruction")
    return report


__all__ = [
    "BODY_CARD_SIGNED_PAIR_MAX_BYTES_V3",
    "BODY_CARD_SIGNED_PAIR_SCHEMA_V3",
    "serialize_signed_body_card_review_pair_v3",
    "verify_and_select_signed_body_card_review_pair_bytes_v3",
]
