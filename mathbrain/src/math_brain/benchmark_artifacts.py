"""Atomic, verifiable materialization for the deterministic synthetic benchmark bank.

Two guarantees are kept apart. Exception atomicity: a failed certificate or write leaves no
destination. Crash durability: the files are fsynced before the rename, and after it the parent
directory is fsynced where the platform offers that (POSIX). Windows has no directory fsync; there
the rename is atomic but not a persistence barrier, and the result says so in ``durability``.
``verify_materialized_benchmark`` checks a directory jointly against a regenerated bank.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .benchmark import (
    BENCHMARK_CAPACITY,
    GENERATOR_BUNDLE_HASH,
    GENERATOR_SOURCE_HASH,
    GENERATOR_VERSION,
    BenchmarkManifest,
    build_manifest,
    generate_bank,
)
from .models import canonical_json


ARTIFACT_SCHEMA = "org.math-research-brain/benchmark-artifact"
ARTIFACT_SCHEMA_VERSION = 1
ARTIFACT_FILES = frozenset({"items.jsonl", "manifest.json", "provenance.json"})
MAX_PROVENANCE_BYTES = 64_000


class BenchmarkDurabilityError(OSError):
    """The bank was published under its name, but its directory entry could not be made durable.

    The destination is complete and verifiable; whether it survives a host crash is unknown.
    """

    def __init__(self, destination: Path, cause: BaseException) -> None:
        super().__init__(f"benchmark written but its directory entry is not durable: {cause}")
        self.destination = destination


@dataclass(frozen=True, slots=True)
class MaterializedBenchmark:
    directory: Path
    items_path: Path
    manifest_path: Path
    provenance_path: Path
    manifest: BenchmarkManifest
    items_digest: str
    items_bytes: int
    durability: str = "unknown"


def _write_canonical(path: Path, payload: object) -> None:
    raw = (canonical_json(payload) + "\n").encode("utf-8")
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _directory_sync_supported() -> bool:
    return os.name != "nt"


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def materialize_benchmark(
    output_directory: str | Path,
    *,
    seed: str,
    count: int,
    start: int = 0,
) -> MaterializedBenchmark:
    """Write one certified JSONL bank and its manifest as an atomic directory.

    The destination must not exist. Items are generated once, streamed into a
    private staging directory, and simultaneously consumed by ``build_manifest``.
    A failed certificate or write leaves no apparently complete destination.
    After the rename the parent directory is fsynced where supported; the result's
    ``durability`` is ``"parent-fsync"`` there and ``"unavailable"`` where the
    platform has no directory fsync. A failed directory fsync raises
    ``BenchmarkDurabilityError``; the destination is then complete but its
    persistence across a crash is unknown.
    """

    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not 1 <= count <= BENCHMARK_CAPACITY
    ):
        raise ValueError(f"count must be in [1, {BENCHMARK_CAPACITY}]")
    if isinstance(start, bool) or not isinstance(start, int) or start < 0:
        raise ValueError("start must be a nonnegative exact integer")
    if start + count > BENCHMARK_CAPACITY:
        raise ValueError("requested window exceeds benchmark capacity")
    if not isinstance(seed, str) or not seed:
        raise ValueError("seed must be nonempty text")

    destination = Path(output_directory).resolve(strict=False)
    if destination.exists():
        raise FileExistsError(f"benchmark destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=destination.parent,
        )
    )
    items_path = staging / "items.jsonl"
    manifest_path = staging / "manifest.json"
    provenance_path = staging / "provenance.json"
    digest = hashlib.sha256()
    item_bytes = 0

    try:
        with items_path.open("xb") as handle:

            def emitted_items():
                nonlocal item_bytes
                for item in generate_bank(seed, count, start=start):
                    raw = (item.canonical_json() + "\n").encode("utf-8")
                    handle.write(raw)
                    digest.update(raw)
                    item_bytes += len(raw)
                    yield item

            manifest = build_manifest(emitted_items(), seed=seed, start=start)
            handle.flush()
            os.fsync(handle.fileno())

        items_digest = "sha256:" + digest.hexdigest()
        _write_canonical(manifest_path, manifest.to_dict())
        _write_canonical(
            provenance_path,
            {
                "generator_bundle_hash": GENERATOR_BUNDLE_HASH,
                "generator_source_hash": GENERATOR_SOURCE_HASH,
                "generator_version": GENERATOR_VERSION,
                "item_count": count,
                "items_bytes": item_bytes,
                "items_digest": items_digest,
                "manifest_digest": manifest.digest(),
                "schema": ARTIFACT_SCHEMA,
                "schema_version": ARTIFACT_SCHEMA_VERSION,
                "seed": seed,
                "start_index": start,
            },
        )
        os.replace(staging, destination)
    except BaseException:
        if staging.exists() and staging.parent == destination.parent:
            shutil.rmtree(staging)
        raise

    durability = "unavailable"
    if _directory_sync_supported():
        try:
            _fsync_directory(destination.parent)
        except OSError as exc:
            raise BenchmarkDurabilityError(destination, exc) from exc
        durability = "parent-fsync"

    return MaterializedBenchmark(
        directory=destination,
        items_path=destination / items_path.name,
        manifest_path=destination / manifest_path.name,
        provenance_path=destination / provenance_path.name,
        manifest=manifest,
        items_digest=items_digest,
        items_bytes=item_bytes,
        durability=durability,
    )


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key in provenance: {key}")
        result[key] = value
    return result


def verify_materialized_benchmark(directory: str | Path) -> BenchmarkManifest:
    """Check a materialized bank jointly and return its manifest, or raise ValueError.

    The directory must hold exactly the three artifact files as regular files. The
    provenance must name this schema and this generator; the bank is regenerated from
    its seed, window and count, and the items and manifest bytes must equal the
    regenerated ones, with the recorded digests and byte count.
    """

    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("benchmark artifact is not a directory")
    names = {entry.name for entry in root.iterdir()}
    if names != ARTIFACT_FILES:
        missing = sorted(ARTIFACT_FILES - names)
        extra = sorted(names - ARTIFACT_FILES)
        raise ValueError(f"benchmark artifact files differ: missing {missing}, extra {extra}")
    for name in sorted(ARTIFACT_FILES):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"benchmark artifact file is not a regular file: {name}")
    raw_provenance = (root / "provenance.json").read_bytes()
    if len(raw_provenance) > MAX_PROVENANCE_BYTES:
        raise ValueError("benchmark provenance is too large")
    try:
        provenance = json.loads(
            raw_provenance.decode("utf-8"), object_pairs_hook=_no_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"benchmark provenance is not JSON: {exc}") from exc
    if not isinstance(provenance, dict):
        raise ValueError("benchmark provenance is not an object")
    identity = {
        "schema": ARTIFACT_SCHEMA,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "generator_bundle_hash": GENERATOR_BUNDLE_HASH,
        "generator_source_hash": GENERATOR_SOURCE_HASH,
        "generator_version": GENERATOR_VERSION,
    }
    for key, value in identity.items():
        if provenance.get(key) != value:
            raise ValueError(f"benchmark provenance {key} does not match this generator")
    seed = provenance.get("seed")
    start = provenance.get("start_index")
    count = provenance.get("item_count")
    if not isinstance(seed, str) or not seed:
        raise ValueError("benchmark provenance seed is missing")
    for label, value in (("start_index", start), ("item_count", count)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"benchmark provenance {label} is not a nonnegative integer")
    if not 1 <= count <= BENCHMARK_CAPACITY or start + count > BENCHMARK_CAPACITY:
        raise ValueError("benchmark provenance window exceeds capacity")

    items_raw = (root / "items.jsonl").read_bytes()
    if len(items_raw) != provenance.get("items_bytes"):
        raise ValueError("benchmark items byte count does not match its provenance")
    items_digest = "sha256:" + hashlib.sha256(items_raw).hexdigest()
    if not hmac.compare_digest(items_digest, str(provenance.get("items_digest"))):
        raise ValueError("benchmark items digest does not match its provenance")
    regenerated = tuple(generate_bank(seed, count, start=start))
    expected_items = b"".join(
        (item.canonical_json() + "\n").encode("utf-8") for item in regenerated
    )
    if not hmac.compare_digest(expected_items, items_raw):
        raise ValueError("benchmark items differ from the regenerated bank")
    manifest = build_manifest(iter(regenerated), seed=seed, start=start)
    expected_manifest = (canonical_json(manifest.to_dict()) + "\n").encode("utf-8")
    if not hmac.compare_digest(expected_manifest, (root / "manifest.json").read_bytes()):
        raise ValueError("benchmark manifest differs from the regenerated manifest")
    if not hmac.compare_digest(manifest.digest(), str(provenance.get("manifest_digest"))):
        raise ValueError("benchmark manifest digest does not match its provenance")
    return manifest


__all__ = [
    "ARTIFACT_FILES",
    "ARTIFACT_SCHEMA",
    "ARTIFACT_SCHEMA_VERSION",
    "BenchmarkDurabilityError",
    "MaterializedBenchmark",
    "materialize_benchmark",
    "verify_materialized_benchmark",
]
