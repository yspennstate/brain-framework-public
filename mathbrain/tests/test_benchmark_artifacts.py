from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from math_brain.benchmark import BenchmarkItem, BenchmarkManifest, verify_manifest
from math_brain.benchmark_artifacts import (
    ARTIFACT_SCHEMA,
    ARTIFACT_SCHEMA_VERSION,
    materialize_benchmark,
)


def test_materializer_writes_one_certified_atomic_artifact(tmp_path: Path) -> None:
    destination = tmp_path / "bank"
    artifact = materialize_benchmark(
        destination,
        seed="artifact-regression",
        count=12,
    )
    assert artifact.directory == destination.resolve()
    lines = artifact.items_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 12
    items = tuple(BenchmarkItem.from_json(line) for line in lines)
    assert len({item.item_id for item in items}) == 12
    raw_items = artifact.items_path.read_bytes()
    assert artifact.items_digest == "sha256:" + hashlib.sha256(raw_items).hexdigest()

    manifest = BenchmarkManifest.from_json(
        artifact.manifest_path.read_text(encoding="utf-8")
    )
    assert manifest == artifact.manifest
    assert verify_manifest(manifest)
    provenance = json.loads(artifact.provenance_path.read_text(encoding="utf-8"))
    assert provenance["schema"] == ARTIFACT_SCHEMA
    assert provenance["schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert provenance["items_digest"] == artifact.items_digest
    assert provenance["manifest_digest"] == manifest.digest()


def test_materializer_refuses_overwrite_and_invalid_windows(tmp_path: Path) -> None:
    destination = tmp_path / "bank"
    materialize_benchmark(destination, seed="no-overwrite", count=1)
    with pytest.raises(FileExistsError):
        materialize_benchmark(destination, seed="no-overwrite", count=1)
    with pytest.raises(ValueError, match="count"):
        materialize_benchmark(tmp_path / "zero", seed="invalid", count=0)
    with pytest.raises(ValueError, match="capacity"):
        materialize_benchmark(
            tmp_path / "overflow",
            seed="invalid",
            count=2,
            start=9_999,
        )
