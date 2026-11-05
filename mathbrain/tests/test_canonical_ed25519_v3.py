from __future__ import annotations

import json

import pytest

import math_brain.canonical_ed25519_v3 as signing
from math_brain.canonical_ed25519_v3 import (
    CanonicalEd25519SignerV3,
    CanonicalEd25519VerifierV3,
)
from math_brain.models import canonical_json

ROLE_A_DOMAIN = "org.math-research-brain/mathnet-language-review/reviewer-a/v3"
ROLE_B_DOMAIN = "org.math-research-brain/mathnet-language-review/reviewer-b/v3"


def _record(**changes: object) -> bytes:
    value: dict[str, object] = {
        "decisions": [
            {
                "audit_item_id": "mathnet-language-audit:" + "a" * 64,
                "verdict": "english",
            }
        ],
        "projection_content_digest": "sha256:" + "b" * 64,
        "reviewer_role": "reviewer_a",
        "schema_version": 3,
    }
    value.update(changes)
    return canonical_json(value).encode("utf-8")


def test_signature_commitment_and_fingerprint_have_fixed_golden_vectors() -> None:
    signer = CanonicalEd25519SignerV3(bytes(range(32)), domain=ROLE_A_DOMAIN)
    verifier = signer.verifier()
    record = _record()
    signature = signer.sign(record)

    assert signer.authority_commitment == verifier.authority_commitment
    assert signer.key_fingerprint == verifier.key_fingerprint
    assert verifier.public_key_bytes() == bytes.fromhex(
        "03a107bff3ce10be1d70dd18e74bc09967e4d6309ba50d5f1ddc8664125531b8"
    )
    assert signer.authority_commitment == (
        "sha256:38ee3fed02aab2a35bd058043c6721c344f07f0548438960d72c1184c707455a"
    )
    assert signer.key_fingerprint == (
        "sha256:3c71651176289f5c6c9bff912e064818de1f56a6e066d341596b6f0ff5553341"
    )
    assert signature == (
        "ed25519:3130e99c6b9d80e59b72c36fb17b366d20428c43ab590076ed793d4ace2750d9"
        "066a3545b7ceb1de4a8e674ad6ef9004b083213093b63c6fbd3c891774f16d0a"
    )
    verifier.verify(record, signature)


def test_record_key_and_domain_mutation_fail_closed() -> None:
    signer = CanonicalEd25519SignerV3(bytes(range(32)), domain=ROLE_A_DOMAIN)
    record = _record()
    signature = signer.sign(record)

    with pytest.raises(ValueError, match="signature is invalid"):
        CanonicalEd25519SignerV3(bytes(range(1, 33)), domain=ROLE_A_DOMAIN).verifier().verify(
            record,
            signature,
        )
    with pytest.raises(ValueError, match="signature is invalid"):
        CanonicalEd25519VerifierV3(
            signer.verifier().public_key_bytes(),
            domain=ROLE_B_DOMAIN,
        ).verify(record, signature)
    with pytest.raises(ValueError, match="signature is invalid"):
        signer.verifier().verify(_record(reviewer_role="reviewer_b"), signature)
    for malformed in (
        "",
        "ed25519:" + "A" * 128,
        "ed25519:" + "0" * 126,
        "sha256:" + "0" * 64,
    ):
        with pytest.raises(ValueError, match="signature"):
            signer.verifier().verify(record, malformed)


def test_same_raw_key_is_visible_across_role_domains() -> None:
    seed = bytes(range(32))
    role_a = CanonicalEd25519SignerV3(seed, domain=ROLE_A_DOMAIN)
    role_b = CanonicalEd25519SignerV3(seed, domain=ROLE_B_DOMAIN)
    assert role_a.key_fingerprint == role_b.key_fingerprint
    assert role_a.authority_commitment != role_b.authority_commitment


@pytest.mark.parametrize(
    "domain",
    (
        "",
        "UPPERCASE",
        "contains space",
        "contains\x00nul",
        "café",
        "a" * 129,
    ),
)
def test_domain_and_key_inputs_are_exact(domain: str) -> None:
    with pytest.raises(ValueError, match="domain"):
        CanonicalEd25519SignerV3(bytes(range(32)), domain=domain)
    with pytest.raises(ValueError, match="domain"):
        CanonicalEd25519VerifierV3(bytes(range(32)), domain=domain)

    with pytest.raises(ValueError, match="private seed"):
        CanonicalEd25519SignerV3(b"short", domain=ROLE_A_DOMAIN)
    with pytest.raises(ValueError, match="private seed"):
        CanonicalEd25519SignerV3(bytearray(range(32)), domain=ROLE_A_DOMAIN)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="public key"):
        CanonicalEd25519VerifierV3(b"short", domain=ROLE_A_DOMAIN)


@pytest.mark.parametrize(
    "record",
    (
        b'{"a":1, "b":2}',
        b'{"a":1}\n',
        b'{"a":1,"a":1}',
        b'{"a":NaN}',
        b'{"a":Infinity}',
        b'{"a":1.0}',
        b'{"a":"\\ud800"}',
        b'[]',
        b'null',
    ),
)
def test_noncanonical_ambiguous_or_untyped_records_are_rejected(record: bytes) -> None:
    signer = CanonicalEd25519SignerV3(bytes(range(32)), domain=ROLE_A_DOMAIN)
    with pytest.raises(ValueError, match="canonical Ed25519 record"):
        signer.sign(record)


def test_unicode_code_points_are_preserved_without_normalization() -> None:
    signer = CanonicalEd25519SignerV3(bytes(range(32)), domain=ROLE_A_DOMAIN)
    nfc = canonical_json({"text": "Café"}).encode("utf-8")
    nfd = canonical_json({"text": "Cafe\u0301"}).encode("utf-8")
    assert nfc != nfd
    assert signer.sign(nfc) != signer.sign(nfd)
    signer.verifier().verify(nfc, signer.sign(nfc))
    signer.verifier().verify(nfd, signer.sign(nfd))


def test_size_depth_and_node_budgets_fail_before_signing(monkeypatch) -> None:
    signer = CanonicalEd25519SignerV3(bytes(range(32)), domain=ROLE_A_DOMAIN)
    valid = _record()
    monkeypatch.setattr(signing, "CANONICAL_ED25519_MAX_RECORD_BYTES_V3", len(valid) - 1)
    with pytest.raises(ValueError, match="byte budget"):
        signer.sign(valid)

    monkeypatch.setattr(signing, "CANONICAL_ED25519_MAX_RECORD_BYTES_V3", 16 * 1024 * 1024)
    monkeypatch.setattr(signing, "CANONICAL_ED25519_MAX_DEPTH_V3", 2)
    deep = canonical_json({"a": {"b": {"c": 1}}}).encode("utf-8")
    with pytest.raises(ValueError, match="invalid"):
        signer.sign(deep)

    monkeypatch.setattr(signing, "CANONICAL_ED25519_MAX_DEPTH_V3", 32)
    monkeypatch.setattr(signing, "CANONICAL_ED25519_MAX_NODES_V3", 2)
    wide = canonical_json({"a": 1, "b": 2}).encode("utf-8")
    with pytest.raises(ValueError, match="invalid"):
        signer.sign(wide)


def test_verifier_has_no_split_public_key_slot() -> None:
    verifier = CanonicalEd25519SignerV3(bytes(range(32)), domain=ROLE_A_DOMAIN).verifier()
    with pytest.raises(AttributeError):
        object.__setattr__(verifier, "_public_key", object())
    with pytest.raises(AttributeError):
        object.__setattr__(verifier, "verify", lambda *_args: None)


def test_identity_torsion_and_noncanonical_public_keys_are_rejected() -> None:
    weak_keys = (
        b"\x01" + b"\x00" * 31,
        b"\x00" * 32,
        b"\xee" + b"\xff" * 30 + b"\x7f",
    )
    for public_key in weak_keys:
        with pytest.raises(ValueError, match="public key"):
            CanonicalEd25519VerifierV3(public_key, domain=ROLE_A_DOMAIN)


def test_small_order_r_and_noncanonical_scalar_are_rejected() -> None:
    signer = CanonicalEd25519SignerV3(bytes(range(32)), domain=ROLE_A_DOMAIN)
    record = _record()
    valid_signature = bytes.fromhex(signer.sign(record).removeprefix("ed25519:"))
    identity_r = b"\x01" + b"\x00" * 31
    with pytest.raises(ValueError, match="R point"):
        signer.verifier().verify(
            record,
            "ed25519:" + (identity_r + b"\x00" * 32).hex(),
        )
    with pytest.raises(ValueError, match="scalar"):
        signer.verifier().verify(
            record,
            "ed25519:"
            + (valid_signature[:32] + signing._ED25519_ORDER.to_bytes(32, "little")).hex(),
        )


def test_canonical_json_float_is_rejected_even_when_json_library_emits_it() -> None:
    raw = json.dumps({"finite": 0.5}, separators=(",", ":"), sort_keys=True).encode()
    signer = CanonicalEd25519SignerV3(bytes(range(32)), domain=ROLE_A_DOMAIN)
    with pytest.raises(ValueError, match="invalid"):
        signer.sign(raw)
