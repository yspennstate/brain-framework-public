from __future__ import annotations

import json
import pickle
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

import math_brain.mathnet_acquisition_v3 as acquisition
from math_brain.mathnet_acquisition_v3 import (
    EXPECTED_MATHNET_SHARDS_V3,
    MATHNET_ARROW_SCHEMA_DIGEST_V3,
    MATHNET_CONFIG_V3,
    MATHNET_PHYSICAL_LEAVES_V3,
    MATHNET_REPOSITORY_V3,
    MATHNET_REVISION_V3,
    MATHNET_ROW_ADAPTER_DIGEST_V3,
    MATHNET_ROW_PROJECTION_V3,
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
    build_pinned_mathnet_bank_v3,
    prepare_pinned_mathnet_source_v3,
    verify_mathnet_remote_metadata_v3,
)


def _remote_sibling(expected):
    return SimpleNamespace(
        rfilename=expected.path,
        size=expected.size_bytes,
        blob_id=expected.git_blob_oid,
        lfs=SimpleNamespace(size=expected.size_bytes, sha256=expected.lfs_sha256),
    )


def _remote_info():
    return SimpleNamespace(
        id=MATHNET_REPOSITORY_V3,
        sha=MATHNET_REVISION_V3,
        siblings=[_remote_sibling(item) for item in EXPECTED_MATHNET_SHARDS_V3],
    )


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


def _runtime_evidence() -> MathNetAcquisitionRuntimeEvidenceV3:
    return MathNetAcquisitionRuntimeEvidenceV3(
        cache_mode=MathNetCacheModeV3.REQUIRE_PRESENT,
        remote_metadata_verified=False,
        immutable_snapshot_enforced=False,
        huggingface_hub_version="1.8.0",
        pyarrow_version="23.0.1",
    )


def test_frozen_shard_pin_is_complete_and_self_consistent() -> None:
    assert len(EXPECTED_MATHNET_SHARDS_V3) == MATHNET_SHARD_COUNT_V3 == 56
    assert tuple(item.ordinal for item in EXPECTED_MATHNET_SHARDS_V3) == tuple(range(56))
    assert tuple(item.row_count for item in EXPECTED_MATHNET_SHARDS_V3) == (
        MATHNET_SHARD_ROWS_V3
    )
    assert sum(item.size_bytes for item in EXPECTED_MATHNET_SHARDS_V3) == (
        MATHNET_TOTAL_BYTES_V3
    )
    assert sum(item.row_count for item in EXPECTED_MATHNET_SHARDS_V3) == (
        MATHNET_TOTAL_ROWS_V3
    )
    assert EXPECTED_MATHNET_SHARDS_V3[0].git_blob_oid == (
        "679d415bcecb16506ed4174d6d88f68c7706975e"
    )
    assert EXPECTED_MATHNET_SHARDS_V3[53].lfs_sha256 == (
        "5a367829c7ccf49e1984fead201278be20cc5e4568dbb38fda969bc2b2dd6e2f"
    )


def test_remote_metadata_verifier_requires_every_exact_lfs_record() -> None:
    assert verify_mathnet_remote_metadata_v3(_remote_info()) == EXPECTED_MATHNET_SHARDS_V3

    variants = []
    wrong_repository = _remote_info()
    wrong_repository.id = "attacker/MathNet"
    variants.append(wrong_repository)
    wrong_revision = _remote_info()
    wrong_revision.sha = "0" * 40
    variants.append(wrong_revision)
    missing = _remote_info()
    missing.siblings.pop()
    variants.append(missing)
    duplicate = _remote_info()
    duplicate.siblings[1].rfilename = duplicate.siblings[0].rfilename
    variants.append(duplicate)
    wrong_hash = _remote_info()
    wrong_hash.siblings[7].lfs.sha256 = "0" * 64
    variants.append(wrong_hash)
    wrong_blob = _remote_info()
    wrong_blob.siblings[9].blob_id = "0" * 40
    variants.append(wrong_blob)
    bool_size = _remote_info()
    bool_size.siblings[10].size = True
    variants.append(bool_size)
    float_sizes = _remote_info()
    for sibling in float_sizes.siblings:
        sibling.size = float(sibling.size)
        sibling.lfs.size = float(sibling.lfs.size)
    variants.append(float_sizes)

    class LyingText(str):
        def __eq__(self, other):
            return True

    lying_digest = _remote_info()
    lying_digest.siblings[3].lfs.sha256 = LyingText("0" * 64)
    variants.append(lying_digest)

    for variant in variants:
        with pytest.raises(ValueError):
            verify_mathnet_remote_metadata_v3(variant)


def test_physical_and_recursive_arrow_schema_pins_are_exact() -> None:
    import pyarrow as pa

    schema = pa.schema(
        [
            pa.field("id", pa.string(), nullable=True),
            pa.field("problem_markdown", pa.string(), nullable=True),
            pa.field(
                "solutions_markdown",
                pa.list_(pa.field("element", pa.string(), nullable=True)),
                nullable=True,
            ),
            pa.field(
                "images",
                pa.list_(
                    pa.field(
                        "element",
                        pa.struct(
                            [
                                pa.field("bytes", pa.binary(), nullable=True),
                                pa.field("path", pa.string(), nullable=True),
                            ]
                        ),
                        nullable=True,
                    )
                ),
                nullable=True,
            ),
            pa.field("country", pa.string(), nullable=True),
            pa.field("competition", pa.string(), nullable=True),
            pa.field(
                "topics_flat",
                pa.list_(pa.field("element", pa.string(), nullable=True)),
                nullable=True,
            ),
            pa.field("language", pa.string(), nullable=True),
            pa.field("problem_type", pa.string(), nullable=True),
            pa.field("final_answer", pa.string(), nullable=True),
        ]
    )
    descriptor = tuple(
        acquisition._arrow_field_descriptor_v3(field, pa) for field in schema
    )
    assert acquisition._digest(
        descriptor,
        domain=acquisition._ARROW_SCHEMA_DOMAIN,
    ) == MATHNET_ARROW_SCHEMA_DIGEST_V3

    changed = schema.set(0, pa.field("id", pa.string(), nullable=False))
    changed_descriptor = tuple(
        acquisition._arrow_field_descriptor_v3(field, pa) for field in changed
    )
    assert acquisition._digest(
        changed_descriptor,
        domain=acquisition._ARROW_SCHEMA_DOMAIN,
    ) != MATHNET_ARROW_SCHEMA_DIGEST_V3


def test_projection_is_path_only_and_forbids_the_silent_wrong_name() -> None:
    assert "images.list.element.path" in MATHNET_ROW_PROJECTION_V3
    assert "images" not in MATHNET_ROW_PROJECTION_V3
    assert "images.path" not in MATHNET_ROW_PROJECTION_V3
    assert not any(item.endswith(".bytes") for item in MATHNET_ROW_PROJECTION_V3)


def test_manifest_is_content_free_and_tamper_evident() -> None:
    manifest = _manifest()
    manifest.validate_current()
    serialized = json.dumps(manifest.to_dict(), sort_keys=True)
    assert manifest.content_digest.startswith("sha256:")
    for forbidden in (
        "SENTINEL_PRIVATE_PROBLEM",
        "SENTINEL_PRIVATE_ANSWER",
        "C:\\Users\\private-sentinel",
        "image-private-path.png",
    ):
        assert forbidden not in serialized

    changed_footer = replace(manifest.footer_audits[0], created_by="different writer")
    tampered = replace(
        manifest,
        footer_audits=(changed_footer, *manifest.footer_audits[1:]),
    )
    assert tampered.content_digest != manifest.content_digest
    object.__setattr__(tampered, "content_digest", manifest.content_digest)
    with pytest.raises(ValueError, match="stale"):
        tampered.validate_current()


def test_footer_and_manifest_reject_schema_or_count_drift() -> None:
    with pytest.raises(ValueError):
        FooterAuditV3(
            ordinal=0,
            row_count=MATHNET_SHARD_ROWS_V3[0] - 1,
            row_group_count=1,
            physical_leaves=MATHNET_PHYSICAL_LEAVES_V3,
            arrow_schema_digest=MATHNET_ARROW_SCHEMA_DIGEST_V3,
            created_by=None,
        )
    with pytest.raises(ValueError):
        replace(_manifest(), unique_id_count=MATHNET_TOTAL_ROWS_V3 - 1)


def test_only_acquisition_can_construct_a_prepared_official_source() -> None:
    with pytest.raises(TypeError):
        PreparedPinnedMathNetSourceV3()
    with pytest.raises(ValueError, match="prepared source"):
        build_pinned_mathnet_bank_v3(
            object(),
            sample_size=1,
            selection_seed=b"x" * 32,
        )


def test_prepared_holder_is_not_dataclass_serializable_or_pickleable() -> None:
    fingerprints = tuple(
        acquisition._LocalShardFingerprintV3(
            size_bytes=item.size_bytes,
            modified_ns=0,
            device=0,
            inode=item.ordinal,
            sha256=item.lfs_sha256,
        )
        for item in EXPECTED_MATHNET_SHARDS_V3
    )
    paths = tuple(
        Path(f"C:/private-mathnet-cache/shard-{item.ordinal:05d}.parquet")
        for item in EXPECTED_MATHNET_SHARDS_V3
    )
    prepared = PreparedPinnedMathNetSourceV3._create(
        manifest=_manifest(),
        runtime_evidence=_runtime_evidence(),
        local_paths=paths,
        fingerprints=fingerprints,
    )
    assert "private-mathnet-cache" not in repr(prepared)
    with pytest.raises(TypeError):
        asdict(prepared)
    with pytest.raises(TypeError, match="non-pickleable"):
        pickle.dumps(prepared)


def test_runtime_versions_do_not_change_stable_source_identity() -> None:
    manifest = _manifest()
    first = _runtime_evidence()
    second = replace(first, pyarrow_version="23.0.2")
    assert second.content_digest != first.content_digest
    assert _manifest().content_digest == manifest.content_digest
    assert first.remote_metadata_verified is False
    assert first.immutable_snapshot_enforced is False


def test_cache_mode_is_exact_before_any_network_or_file_work() -> None:
    with pytest.raises(ValueError, match="cache mode"):
        prepare_pinned_mathnet_source_v3(cache_mode="require_present")  # type: ignore[arg-type]
    assert MathNetCacheModeV3.REQUIRE_PRESENT.value == "require_present"
