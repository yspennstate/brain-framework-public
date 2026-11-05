from __future__ import annotations

import hashlib
import json
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import math_brain.mathnet_acquisition_v3 as acquisition
import math_brain.mathnet_bank_v3 as bank_module
import math_brain.mathnet_random_external_lane_v4 as lane
from math_brain.internal_pilot_selection_v3 import (
    InternalExternalSelectionV3,
    adapt_internal_random_external_selection_v3,
    verify_internal_random_external_selection_v3,
)
from math_brain.mathnet_acquisition_v3 import (
    EXPECTED_MATHNET_SHARDS_V3,
    MATHNET_ARROW_SCHEMA_DIGEST_V3,
    MATHNET_CONFIG_V3,
    MATHNET_PHYSICAL_LEAVES_V3,
    MATHNET_REPOSITORY_V3,
    MATHNET_REVISION_V3,
    MATHNET_ROW_ADAPTER_DIGEST_V3,
    MATHNET_SHARD_COUNT_V3,
    MATHNET_SHARD_ROWS_V3,
    MATHNET_SPLIT_V3,
    MATHNET_TOTAL_BYTES_V3,
    MATHNET_TOTAL_ROWS_V3,
    FooterAuditV3,
    MathNetAcquisitionRuntimeEvidenceV3,
    MathNetCacheModeV3,
    MathNetSourceArtifactManifestV3,
    PreparedPinnedMathNetSourceV3,
    VerifiedPinnedMathNetBankV3,
)
from math_brain.mathnet_body_card_review_v3 import BodyCardSelectionStatusV3
from math_brain.models import canonical_json


def _manifest() -> MathNetSourceArtifactManifestV3:
    footers = tuple(
        FooterAuditV3(
            ordinal=ordinal,
            row_count=MATHNET_SHARD_ROWS_V3[ordinal],
            row_group_count=1,
            physical_leaves=MATHNET_PHYSICAL_LEAVES_V3,
            arrow_schema_digest=MATHNET_ARROW_SCHEMA_DIGEST_V3,
            created_by="parquet-cpp-arrow version 23.0.1",
        )
        for ordinal in range(MATHNET_SHARD_COUNT_V3)
    )
    return MathNetSourceArtifactManifestV3(
        repository_id=MATHNET_REPOSITORY_V3,
        revision=MATHNET_REVISION_V3,
        config_name=MATHNET_CONFIG_V3,
        split=MATHNET_SPLIT_V3,
        shards=EXPECTED_MATHNET_SHARDS_V3,
        footer_audits=footers,
        total_bytes=MATHNET_TOTAL_BYTES_V3,
        total_rows=MATHNET_TOTAL_ROWS_V3,
        unique_id_count=MATHNET_TOTAL_ROWS_V3,
        arrow_schema_digest=MATHNET_ARROW_SCHEMA_DIGEST_V3,
        row_adapter_digest=MATHNET_ROW_ADAPTER_DIGEST_V3,
    )


def _runtime() -> MathNetAcquisitionRuntimeEvidenceV3:
    return MathNetAcquisitionRuntimeEvidenceV3(
        cache_mode=MathNetCacheModeV3.REQUIRE_PRESENT,
        remote_metadata_verified=False,
        immutable_snapshot_enforced=False,
        huggingface_hub_version="1.8.0",
        pyarrow_version="23.0.1",
    )


@pytest.fixture(scope="module")
def prepared_source(tmp_path_factory: pytest.TempPathFactory) -> PreparedPinnedMathNetSourceV3:
    manifest = _manifest()
    runtime = _runtime()
    root = tmp_path_factory.mktemp("random-external-source").resolve()
    paths = tuple(root / f"shard-{index:02d}.parquet" for index in range(56))
    fingerprints = tuple(
        acquisition._LocalShardFingerprintV3(
            size_bytes=index,
            modified_ns=index,
            device=1,
            inode=index,
            sha256="00" * 32,
        )
        for index in range(56)
    )
    return PreparedPinnedMathNetSourceV3._create(
        manifest=manifest,
        runtime_evidence=runtime,
        local_paths=paths,
        fingerprints=fingerprints,
    )


def _row(index: int) -> dict[str, object]:
    eligible = index < lane.MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4
    return {
        "id": f"random-v4-{index:05d}",
        "problem_markdown": f"Compute the exact value of {index} - {index - 1}.",
        "solutions_markdown": [r"Subtracting gives \boxed{1}."],
        "images": [],
        "country": f"Eligible-Country-{index}" if eligible else "France",
        "competition": f"Eligible-Contest-{index}" if eligible else f"Archive-{index}",
        "topics_flat": ["Algebra"],
        "language": "English" if eligible else "French",
        "problem_type": "final answer only",
        "final_answer": "1",
    }


@pytest.fixture(scope="module")
def source_rows() -> tuple[dict[str, object], ...]:
    return tuple(_row(index) for index in range(MATHNET_TOTAL_ROWS_V3))


def _relay_responses(
    spec: lane.DrandQuicknetRoundSpecV4,
    *,
    signature_byte: int = 0x42,
) -> tuple[tuple[str, bytes], ...]:
    signature = bytes([signature_byte]) * 48
    value = {
        "randomness": hashlib.sha256(signature).hexdigest(),
        "round": spec.round_number,
        "signature": signature.hex(),
    }
    return (
        (spec.relay_urls[0], canonical_json(value).encode("utf-8")),
        (spec.relay_urls[1], json.dumps(value, indent=2).encode("utf-8")),
    )


@pytest.fixture(scope="module")
def committed_records(
    prepared_source: PreparedPinnedMathNetSourceV3,
) -> tuple[
    lane.MathNetRandomExternalPrecommitmentV4,
    lane.ExternallyAnchoredDrandBeaconReceiptV4,
]:
    code = lane.freeze_current_mathnet_random_selector_code_v4(protocol_git_commit="a" * 40)
    spec = lane.DrandQuicknetRoundSpecV4(round_number=100_000_000)
    precommitment = lane.build_mathnet_random_external_precommitment_v4(
        prepared_source,
        claimed_created_utc="2026-07-15T00:00:00Z",
        selector_code_manifest=code,
        beacon_round_spec=spec,
    )
    responses = _relay_responses(spec)
    observed = spec.scheduled_utc
    anchor_payload = lane.render_mathnet_random_beacon_anchor_payload_v4(
        precommitment,
        relay_responses=responses,
        observed_at_claimed_utc=observed,
    )
    anchored = (
        datetime.strptime(observed, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) + timedelta(seconds=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    receipt = lane.build_externally_anchored_drand_beacon_receipt_v4(
        precommitment,
        relay_responses=responses,
        observed_at_claimed_utc=observed,
        anchor_payload_bytes=anchor_payload,
        anchor_system="generic-public-anchor",
        anchor_locator="https://example.invalid/mathnet-random-v4/beacon",
        anchor_evidence_bytes=b"synthetic external anchor evidence",
        anchor_claimed_utc=anchored,
    )
    return precommitment, receipt


@pytest.fixture(scope="module")
def official_wrapper(
    prepared_source: PreparedPinnedMathNetSourceV3,
    source_rows: tuple[dict[str, object], ...],
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> VerifiedPinnedMathNetBankV3:
    precommitment, receipt = committed_records
    seed = lane.derive_mathnet_random_selection_seed_v4(precommitment, receipt)
    binding = bank_module._official_mathnet_source_binding_v3(
        prepared_source.manifest.content_digest
    )
    bank = bank_module._build_mathnet_bank_v3(
        source_rows,
        source_binding=binding,
        sample_size=lane.MATHNET_RANDOM_EXTERNAL_SAMPLE_SIZE_V4,
        selection_seed=seed,
    )
    return VerifiedPinnedMathNetBankV3._create(
        manifest=prepared_source.manifest,
        runtime_evidence=prepared_source.runtime_evidence,
        bank=bank,
    )


def _install_official_stubs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: PreparedPinnedMathNetSourceV3,
    wrapper: VerifiedPinnedMathNetBankV3,
    expected_seed: bytes,
) -> dict[str, int]:
    calls = {"build": 0, "verify": 0}

    def build(
        observed_source: PreparedPinnedMathNetSourceV3,
        *,
        sample_size: int,
        selection_seed: bytes,
    ) -> VerifiedPinnedMathNetBankV3:
        calls["build"] += 1
        assert observed_source is source
        assert sample_size == 12
        assert selection_seed == expected_seed
        return wrapper

    def verify(
        observed_source: PreparedPinnedMathNetSourceV3,
        observed_wrapper: VerifiedPinnedMathNetBankV3,
        *,
        selection_seed: bytes,
    ) -> None:
        calls["verify"] += 1
        assert observed_source is source
        assert observed_wrapper is wrapper
        assert selection_seed == expected_seed

    monkeypatch.setattr(lane, "build_pinned_mathnet_bank_v3", build)
    monkeypatch.setattr(lane, "verify_pinned_mathnet_bank_v3", verify)
    return calls


def _cold_selection(
    monkeypatch: pytest.MonkeyPatch,
    prepared_source: PreparedPinnedMathNetSourceV3,
    official_wrapper: VerifiedPinnedMathNetBankV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> tuple[lane.ColdReplayMathNetRandomSelectionV4, dict[str, int]]:
    precommitment, receipt = committed_records
    expected_seed = lane.derive_mathnet_random_selection_seed_v4(precommitment, receipt)
    calls = _install_official_stubs(
        monkeypatch,
        source=prepared_source,
        wrapper=official_wrapper,
        expected_seed=expected_seed,
    )
    result = lane.reveal_and_cold_replay_mathnet_random_external_lane_v4(
        precommitment.canonical_record(),
        receipt.canonical_record(),
        expected_precommitment_record_sha256=precommitment.record_sha256,
        expected_beacon_receipt_record_sha256=receipt.record_sha256,
        source=prepared_source,
    )
    return result, calls


def test_precommitment_binds_exact_source_code_round_and_zero_replacement_budgets(
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    precommitment, receipt = committed_records
    parsed = lane.MathNetRandomExternalPrecommitmentV4.from_canonical_record(
        precommitment.canonical_record()
    )
    assert parsed.to_dict() == precommitment.to_dict()
    assert len(parsed.selector_code_manifest.module_sha256s) == 5
    assert parsed.beacon_round_spec.round_number == 100_000_000
    assert parsed.beacon_round_spec.to_dict()["chain_hash"] == (lane.DRAND_QUICKNET_CHAIN_HASH_V4)
    assert parsed.to_dict()["constraints"] == {
        "failed_body_card_artifact_consumption_allowed": False,
        "replacement_budget": 0,
        "reroll_budget": 0,
        "reselection_budget": 0,
        "selection_attempt_limit": 1,
        "topup_budget": 0,
    }
    assert all(value is False for value in parsed.to_dict()["authority"].values())
    replayed_receipt = lane.ExternallyAnchoredDrandBeaconReceiptV4.from_canonical_record(
        receipt.canonical_record()
    )
    assert replayed_receipt.to_dict() == receipt.to_dict()
    assert replayed_receipt.to_dict()["authority"] == {
        "beacon_bls_signature_verified": False,
        "chronology_verified": False,
        "provider_origin_verified": False,
        "randomness_hash_verified": True,
        "relay_agreement_verified": True,
    }


def test_capability_and_gold_input_offer_no_direct_mint_interface() -> None:
    assert not hasattr(lane.ColdReplayMathNetRandomSelectionV4, "_mint")
    assert not hasattr(lane.ColdReplayMathNetRandomSelectionV4, "_derive")
    assert not hasattr(lane.MathNetRandomExternalGoldAuditInputV4, "_mint")
    assert not hasattr(lane.MathNetRandomExternalGoldAuditInputV4, "_derive")
    assert not hasattr(lane, "_mint_gold_input_inside_cold_replay_v4")
    assert not hasattr(lane, "_mint_selection_inside_cold_replay_v4")
    with pytest.raises(TypeError, match="inside official cold replay"):
        lane.ColdReplayMathNetRandomSelectionV4()
    with pytest.raises(TypeError, match="inside cold replay"):
        lane.MathNetRandomExternalGoldAuditInputV4()
    with pytest.raises(TypeError):
        lane.ColdReplayMathNetRandomSelectionV4(
            lane._SELECTION_TOKEN_V4  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        lane.MathNetRandomExternalGoldAuditInputV4(
            lane._GOLD_INPUT_TOKEN_V4  # type: ignore[call-arg]
        )


def test_beacon_receipt_carries_replayable_relay_bytes_and_anchor_payload_binding(
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    _precommitment, receipt = committed_records
    for relay in receipt.relay_receipts:
        assert lane._sha256(relay.response_bytes) == relay.response_sha256
        with pytest.raises(ValueError, match="digest is stale"):
            replace(relay, response_base64="e30=")
    parsed = lane.ExternallyAnchoredDrandBeaconReceiptV4.from_canonical_record(
        receipt.canonical_record()
    )
    object.__setattr__(parsed, "anchor_payload_sha256", "sha256:" + "00" * 32)
    with pytest.raises(ValueError, match="anchor payload digest is stale"):
        parsed.validate_current()


def test_two_relay_receipt_rejects_single_disagreement_and_bad_hash(
    prepared_source: PreparedPinnedMathNetSourceV3,
) -> None:
    code = lane.freeze_current_mathnet_random_selector_code_v4(protocol_git_commit="b" * 40)
    spec = lane.DrandQuicknetRoundSpecV4(round_number=100_000_001)
    precommitment = lane.build_mathnet_random_external_precommitment_v4(
        prepared_source,
        claimed_created_utc="2026-07-15T00:00:00Z",
        selector_code_manifest=code,
        beacon_round_spec=spec,
    )
    responses = _relay_responses(spec)
    with pytest.raises(ValueError, match="both exact"):
        lane.render_mathnet_random_beacon_anchor_payload_v4(
            precommitment,
            relay_responses=responses[:1],
            observed_at_claimed_utc=spec.scheduled_utc,
        )
    disagreement = (responses[0], _relay_responses(spec, signature_byte=0x43)[1])
    with pytest.raises(ValueError, match="disagree"):
        lane.render_mathnet_random_beacon_anchor_payload_v4(
            precommitment,
            relay_responses=disagreement,
            observed_at_claimed_utc=spec.scheduled_utc,
        )
    bad = json.loads(responses[0][1])
    bad["randomness"] = "00" * 32
    bad_responses = (
        (responses[0][0], canonical_json(bad).encode("utf-8")),
        (responses[1][0], canonical_json(bad).encode("utf-8")),
    )
    with pytest.raises(ValueError, match="not SHA-256"):
        lane.render_mathnet_random_beacon_anchor_payload_v4(
            precommitment,
            relay_responses=bad_responses,
            observed_at_claimed_utc=spec.scheduled_utc,
        )


def test_seed_ignores_generic_anchor_evidence_but_changes_with_beacon_randomness(
    prepared_source: PreparedPinnedMathNetSourceV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    precommitment, first = committed_records
    spec = precommitment.beacon_round_spec
    responses = _relay_responses(spec)
    anchor_payload = lane.render_mathnet_random_beacon_anchor_payload_v4(
        precommitment,
        relay_responses=responses,
        observed_at_claimed_utc=spec.scheduled_utc,
    )
    second = lane.build_externally_anchored_drand_beacon_receipt_v4(
        precommitment,
        relay_responses=responses,
        observed_at_claimed_utc=spec.scheduled_utc,
        anchor_payload_bytes=anchor_payload,
        anchor_system="another-public-anchor",
        anchor_locator="https://example.invalid/another-anchor",
        anchor_evidence_bytes=b"different evidence bytes",
        anchor_claimed_utc=first.anchor_claimed_utc,
    )
    assert second.content_digest != first.content_digest
    assert lane.derive_mathnet_random_selection_seed_v4(precommitment, second) == (
        lane.derive_mathnet_random_selection_seed_v4(precommitment, first)
    )
    assert prepared_source.manifest.content_digest == precommitment.source_manifest_digest


def test_genuine_cold_replay_mints_exact_ordered_capability_and_body_only_input(
    monkeypatch: pytest.MonkeyPatch,
    prepared_source: PreparedPinnedMathNetSourceV3,
    official_wrapper: VerifiedPinnedMathNetBankV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    capability, calls = _cold_selection(
        monkeypatch,
        prepared_source,
        official_wrapper,
        committed_records,
    )
    assert calls == {"build": 1, "verify": 1}
    assert type(capability) is lane.ColdReplayMathNetRandomSelectionV4
    capability.validate_current()
    assert tuple(row.selection_index for row in capability.selected_rows) == tuple(range(12))
    assert len({row.case_id for row in capability.selected_rows}) == 12
    assert len({row.source_cluster_digest for row in capability.selected_rows}) == 12
    assert all(row.case_id.startswith("mathnet-random-v4:") for row in capability.selected_rows)
    assert capability.selection_digest == capability.gold_audit_input.selection_digest
    assert capability.private_gold_vault_record_sha256 == (
        capability.gold_audit_input.private_gold_vault_record_sha256
    )
    projection = lane.external_gold_audit_projection_v4(
        capability,
        capability.gold_audit_input,
    )
    assert type(projection) is tuple and len(projection) == 12
    assert all(
        set(item) == {"case_id", "problem_text", "problem_text_digest"} for item in projection
    )
    serialized = capability.gold_audit_input.canonical_record().decode("utf-8")
    for forbidden in (
        "private_gold",
        "final_answer",
        "solutions_markdown",
        "source_cluster_digest",
        "candidate_content_digest",
        "domain",
        "difficulty",
        "BodyCard",
    ):
        if forbidden == "private_gold":
            assert '"private_gold":' not in serialized
        else:
            assert forbidden not in serialized
    authority = capability._payload()["authority"]
    assert authority["cold_replay_verified"] is True
    assert authority["two_relay_agreement_verified"] is True
    assert authority["external_chronology_authority_verified"] is False
    assert authority["beacon_bls_signature_verified"] is False


def test_genuine_cold_replay_capability_adapts_into_internal_selector_binding(
    monkeypatch: pytest.MonkeyPatch,
    prepared_source: PreparedPinnedMathNetSourceV3,
    official_wrapper: VerifiedPinnedMathNetBankV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    capability, calls = _cold_selection(
        monkeypatch,
        prepared_source,
        official_wrapper,
        committed_records,
    )
    precommitment, receipt = committed_records
    precommitment_bytes = precommitment.canonical_record()
    receipt_bytes = receipt.canonical_record()
    selection = adapt_internal_random_external_selection_v3(
        precommitment_bytes,
        receipt_bytes,
        expected_precommitment_record_sha256=precommitment.record_sha256,
        expected_beacon_receipt_record_sha256=receipt.record_sha256,
        expected_selection_capability_digest=capability.content_digest,
        source=prepared_source,
    )

    assert calls == {"build": 2, "verify": 2}
    assert type(selection) is InternalExternalSelectionV3
    selection.validate_current()
    assert selection.random_lane_capability_digest == capability.content_digest
    assert selection.source_manifest_digest == capability.source_manifest_digest
    assert selection.selector_code_manifest_digest == capability.selector_code_manifest_digest
    assert selection.private_gold_vault_record_sha256 == (
        capability.private_gold_vault_record_sha256
    )
    assert tuple(case.case_id for case in selection.cases) == tuple(
        row.case_id for row in capability.selected_rows
    )
    assert tuple(case.source_row_content_digest for case in selection.cases) == tuple(
        row.content_digest for row in capability.selected_rows
    )
    assert selection.content_digest.startswith("sha256:")

    verify_internal_random_external_selection_v3(
        selection,
        precommitment_bytes,
        receipt_bytes,
        expected_precommitment_record_sha256=precommitment.record_sha256,
        expected_beacon_receipt_record_sha256=receipt.record_sha256,
        expected_selection_capability_digest=capability.content_digest,
        source=prepared_source,
    )
    assert calls == {"build": 3, "verify": 3}

    object.__setattr__(selection, "source_manifest_digest", "sha256:" + "f" * 64)
    with pytest.raises(ValueError, match="content digest is stale"):
        _ = selection.content_digest


def test_module_token_forged_nominal_capability_cannot_replace_fresh_internal_replay(
    monkeypatch: pytest.MonkeyPatch,
    prepared_source: PreparedPinnedMathNetSourceV3,
    official_wrapper: VerifiedPinnedMathNetBankV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    capability, calls = _cold_selection(
        monkeypatch,
        prepared_source,
        official_wrapper,
        committed_records,
    )
    forged = object.__new__(lane.ColdReplayMathNetRandomSelectionV4)
    for data_field in fields(lane.ColdReplayMathNetRandomSelectionV4):
        if data_field.name in {"content_digest", "_mint_marker"}:
            continue
        object.__setattr__(forged, data_field.name, getattr(capability, data_field.name))
    object.__setattr__(forged, "beacon_round_number", capability.beacon_round_number + 1)
    object.__setattr__(forged, "_mint_marker", lane._SELECTION_TOKEN_V4)
    object.__setattr__(
        forged,
        "content_digest",
        lane._domain_digest(
            forged._payload(),
            domain="org.math-research-brain/mathnet-random-external-capability/v4",
        ),
    )
    forged.validate_current()
    assert forged.content_digest != capability.content_digest

    precommitment, receipt = committed_records
    with pytest.raises(ValueError, match="external capability anchor"):
        adapt_internal_random_external_selection_v3(
            precommitment.canonical_record(),
            receipt.canonical_record(),
            expected_precommitment_record_sha256=precommitment.record_sha256,
            expected_beacon_receipt_record_sha256=receipt.record_sha256,
            expected_selection_capability_digest=forged.content_digest,
            source=prepared_source,
        )
    assert calls == {"build": 2, "verify": 2}


def test_vault_rebuild_and_strict_post_replay_open_cross_bind_every_case(
    monkeypatch: pytest.MonkeyPatch,
    prepared_source: PreparedPinnedMathNetSourceV3,
    official_wrapper: VerifiedPinnedMathNetBankV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    capability, _calls = _cold_selection(
        monkeypatch,
        prepared_source,
        official_wrapper,
        committed_records,
    )
    precommitment, receipt = committed_records
    raw = lane.rebuild_mathnet_random_external_gold_vault_record_v4(
        precommitment.canonical_record(),
        receipt.canonical_record(),
        expected_precommitment_record_sha256=precommitment.record_sha256,
        expected_beacon_receipt_record_sha256=receipt.record_sha256,
        source=prepared_source,
        capability=capability,
        gold_input=capability.gold_audit_input,
    )
    assert "Compute the exact value" not in raw.decode("utf-8")
    assert lane._sha256(raw) == capability.private_gold_vault_record_sha256
    entries = lane.open_external_gold_vault_entries_after_replay_v4(
        raw,
        capability,
        capability.gold_audit_input,
    )
    assert len(entries) == 12
    assert tuple(item.case_id for item in entries) == tuple(
        row.case_id for row in capability.selected_rows
    )
    assert all(item.private_gold.answer.numerator == 1 for item in entries)
    tampered = bytearray(raw)
    tampered[-2] ^= 1
    with pytest.raises(ValueError, match="replay commitment"):
        lane.open_external_gold_vault_entries_after_replay_v4(
            bytes(tampered),
            capability,
            capability.gold_audit_input,
        )


def test_wrong_external_record_commitment_fails_before_official_builder(
    monkeypatch: pytest.MonkeyPatch,
    prepared_source: PreparedPinnedMathNetSourceV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    precommitment, receipt = committed_records

    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("official bank builder ran before anchor rejection")

    monkeypatch.setattr(lane, "build_pinned_mathnet_bank_v3", forbidden)
    with pytest.raises(ValueError, match="external commitment"):
        lane.reveal_and_cold_replay_mathnet_random_external_lane_v4(
            precommitment.canonical_record(),
            receipt.canonical_record(),
            expected_precommitment_record_sha256="sha256:" + "00" * 32,
            expected_beacon_receipt_record_sha256=receipt.record_sha256,
            source=prepared_source,
        )


def test_code_byte_drift_fails_before_official_builder(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    prepared_source: PreparedPinnedMathNetSourceV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    precommitment, receipt = committed_records
    changed = tmp_path / "changed.py"
    changed.write_bytes(b"changed selector bytes")
    labels = tuple(label for label, _path in lane._module_paths_v4())
    monkeypatch.setattr(
        lane,
        "_module_paths_v4",
        lambda: tuple((label, changed) for label in labels),
    )

    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("official bank builder ran after code drift")

    monkeypatch.setattr(lane, "build_pinned_mathnet_bank_v3", forbidden)
    with pytest.raises(ValueError, match="source bytes differ"):
        lane.reveal_and_cold_replay_mathnet_random_external_lane_v4(
            precommitment.canonical_record(),
            receipt.canonical_record(),
            expected_precommitment_record_sha256=precommitment.record_sha256,
            expected_beacon_receipt_record_sha256=receipt.record_sha256,
            source=prepared_source,
        )


def test_reordered_capability_and_stale_case_id_fail_validation(
    monkeypatch: pytest.MonkeyPatch,
    prepared_source: PreparedPinnedMathNetSourceV3,
    official_wrapper: VerifiedPinnedMathNetBankV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    capability, _calls = _cold_selection(
        monkeypatch,
        prepared_source,
        official_wrapper,
        committed_records,
    )
    object.__setattr__(capability, "selected_rows", tuple(reversed(capability.selected_rows)))
    with pytest.raises(ValueError, match="reordered"):
        capability.validate_current()


def test_legacy_body_card_artifact_is_explicitly_rejected(
    prepared_source: PreparedPinnedMathNetSourceV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    precommitment, receipt = committed_records
    with pytest.raises(ValueError, match="rejects failed body/card"):
        lane.build_mathnet_random_external_precommitment_v4(
            BodyCardSelectionStatusV3.INFEASIBLE,  # type: ignore[arg-type]
            claimed_created_utc="2026-07-15T00:00:00Z",
            selector_code_manifest=precommitment.selector_code_manifest,
            beacon_round_spec=precommitment.beacon_round_spec,
        )
    with pytest.raises(ValueError, match="rejects failed body/card"):
        lane.reveal_and_cold_replay_mathnet_random_external_lane_v4(
            BodyCardSelectionStatusV3.INFEASIBLE,  # type: ignore[arg-type]
            receipt.canonical_record(),
            expected_precommitment_record_sha256=precommitment.record_sha256,
            expected_beacon_receipt_record_sha256=receipt.record_sha256,
            source=prepared_source,
        )


def test_strict_records_reject_noncanonical_extra_and_duplicate_fields(
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    precommitment, _receipt = committed_records
    raw = precommitment.canonical_record()
    with pytest.raises(ValueError, match="canonical encoding"):
        lane.MathNetRandomExternalPrecommitmentV4.from_canonical_record(raw.replace(b"{", b"{ ", 1))
    value = json.loads(raw)
    value["topup_seed"] = "00" * 32
    with pytest.raises(ValueError, match="unexpected fields"):
        lane.MathNetRandomExternalPrecommitmentV4.from_canonical_record(
            canonical_json(value).encode("utf-8")
        )
    duplicate = raw[:-1] + b',"lane_id":"duplicate"}'
    with pytest.raises(ValueError, match="strict JSON"):
        lane.MathNetRandomExternalPrecommitmentV4.from_canonical_record(duplicate)


def test_dataclass_replace_cannot_relabel_external_cases(
    monkeypatch: pytest.MonkeyPatch,
    prepared_source: PreparedPinnedMathNetSourceV3,
    official_wrapper: VerifiedPinnedMathNetBankV3,
    committed_records: tuple[
        lane.MathNetRandomExternalPrecommitmentV4,
        lane.ExternallyAnchoredDrandBeaconReceiptV4,
    ],
) -> None:
    capability, _calls = _cold_selection(
        monkeypatch,
        prepared_source,
        official_wrapper,
        committed_records,
    )
    row = capability.selected_rows[0]
    changed = replace(row, case_id="mathnet-random-v4:" + "00" * 32)
    with pytest.raises(ValueError, match="exact bank coordinate"):
        object.__setattr__(capability, "selected_rows", (changed, *capability.selected_rows[1:]))
        capability.validate_current()
