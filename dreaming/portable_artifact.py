#!/usr/bin/env python3
"""Credential-free, bounded transport of exact Git bytes through text connectors.

The connector supplies immutable manifest/part files. This module supplies no
network access and does not execute transferred code. Pin the manifest digest
from an independently fetched Git object, not from an untrusted bundle itself.
Successful transfer is source integrity, not full runtime readiness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zlib

SCHEMA = "portable-git-bytes-v1"
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096
MAX_PARTS = 8192
MAX_MANIFEST = 2 * 1024 * 1024
DEFAULT_CHUNK = 4 * 1024
RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
            *(f"lpt{i}" for i in range(1, 10))}


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def git_blob(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError("invalid relative path")
    if "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise ValueError("non-portable relative path")
    for part in value.split("/"):
        if part in ("", ".", "..") or part.endswith((" ", ".")):
            raise ValueError("unsafe relative path")
        if part.split(".", 1)[0].lower() in RESERVED:
            raise ValueError("reserved device path")
    return value


def no_links(path: Path, stop: Path | None = None) -> None:
    """Refuse a link at any node from `path` up to and INCLUDING `stop`.

    The property worth defending is that nothing this module creates, and nothing a
    manifest names, is redirected by a link the caller did not intend - and that includes
    the root the caller handed us, so `stop` is checked before the walk ends. What is NOT
    an attack surface we can close is everything ABOVE that root: on macOS every temporary
    directory sits under /var, which is a symlink to /private/var, and on Windows a user
    profile carries junctions for compatibility. A guard that walks to the filesystem root
    therefore refuses the platform rather than an attacker - it made all 25 tests in this
    module error in setUp on macOS while passing everywhere else.

    With no `stop`, the walk still goes to the filesystem root, which stays the right
    default for a path whose own root the caller did not name.
    """
    boundary = None
    if stop is not None:
        try:
            boundary = stop.resolve()
        except OSError:
            boundary = None
    for node in (path, *path.parents):
        if node.is_symlink() or (hasattr(node, "is_junction") and node.is_junction()):
            raise ValueError("symlink or junction path refused")
        if boundary is not None:
            try:
                if node.resolve() == boundary:
                    return
            except OSError:
                pass


def checked_paths(names: list[str]) -> None:
    normalized = [safe_relative(n).casefold() for n in names]
    if len(set(normalized)) != len(names):
        raise ValueError("duplicate or case-colliding paths")
    all_names = set(normalized)
    for name in normalized:
        parts = name.split("/")
        if any("/".join(parts[:n]) in all_names for n in range(1, len(parts))):
            raise ValueError("file/directory path conflict")


def integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("integer outside limit")
    return value


def hex_digest(value: object, length: int = 64) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{%d}" % length, value):
        raise ValueError("invalid digest")
    return value


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def build(files: dict[str, bytes], output: Path, revision: str,
          chunk_size: int = DEFAULT_CHUNK) -> dict:
    hex_digest(revision, 40)
    integer(chunk_size, 64, 64 * 1024)
    integer(len(files), 1, MAX_FILES)
    checked_paths(list(files))
    rows, offset = [], 0
    for name, raw in sorted(files.items()):
        if not isinstance(raw, bytes):
            raise ValueError("file contents must be bytes")
        rows.append({"path": name, "offset": offset, "size": len(raw),
                     "sha256": digest(raw), "git_blob_sha1": git_blob(raw)})
        offset += len(raw)
        integer(offset, 0, MAX_BYTES)
    data = b"".join(files[r["path"]] for r in rows)
    packed = zlib.compress(data, 9)
    pieces, parts = {}, []
    for index, start in enumerate(range(0, len(packed), chunk_size)):
        raw = packed[start:start + chunk_size]
        name = f"part-{index:05d}.hex"
        encoded = raw.hex().encode("ascii") + b"\n"
        pieces[name] = encoded
        parts.append({"index": index, "path": name, "offset": start,
                      "size": len(raw), "sha256": digest(raw),
                      "encoded_size": len(encoded), "encoded_sha256": digest(encoded)})
    manifest = {"schema": SCHEMA, "source_revision": revision,
                "codec": "zlib+hex", "data_bytes": len(data), "data_sha256": digest(data),
                "packed_bytes": len(packed), "packed_sha256": digest(packed),
                "files": rows, "parts": parts}
    manifest_raw = canonical(manifest)
    integer(len(manifest_raw), 1, MAX_MANIFEST)
    no_links(output, output.parent)
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in pieces.items():
        with (output / name).open("xb") as handle:
            handle.write(raw)
    with (output / "manifest.json").open("xb") as handle:
        handle.write(manifest_raw)
    return {"status": "BUNDLE_BUILT", "source_revision": revision,
            "manifest_sha256": digest(manifest_raw), "files": len(rows),
            "source_bytes": len(data), "packed_bytes": len(packed), "parts": len(parts),
            "full_brain_connected": False}


def git_files(repository: Path, revision: str, includes: list[str]) -> dict[str, bytes]:
    """Read an explicit allowlist from immutable Git objects, never working files."""
    hex_digest(revision, 40)
    if not includes:
        raise ValueError("explicit source allowlist required")
    for name in includes:
        safe_relative(name.rstrip("/"))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    def git(*args: str) -> bytes:
        return subprocess.run(["git", "-C", str(repository), *args], check=True,
                              capture_output=True, timeout=60, creationflags=flags).stdout
    resolved = git("rev-parse", "--verify", revision + "^{commit}").decode("ascii").strip()
    if resolved != revision:
        raise ValueError("revision must name the exact commit")
    tree = git("ls-tree", "-rz", "--full-tree", revision, "--", *includes)
    files = {}
    matched = set()
    total = 0
    for row in tree.split(b"\0"):
        if not row:
            continue
        meta, raw_name = row.split(b"\t", 1)
        mode, kind, object_id = meta.decode("ascii").split()
        name = safe_relative(raw_name.decode("utf-8"))
        if mode not in ("100644", "100755") or kind != "blob":
            raise ValueError("only regular source files can be transferred")
        eligible = {p for p in includes if name == p.rstrip("/") or name.startswith(p.rstrip("/") + "/")}
        if not eligible:
            raise ValueError("Git pathspec escaped the explicit source allowlist")
        matched.update(eligible)
        size = int(git("cat-file", "-s", object_id).decode("ascii"))
        total += size
        integer(total, 0, MAX_BYTES)
        integer(len(files) + 1, 1, MAX_FILES)
        raw = git("cat-file", "blob", object_id)
        if len(raw) != size or git_blob(raw) != object_id:
            raise ValueError("Git object integrity mismatch")
        files[name] = raw
    if matched != set(includes):
        raise ValueError("source allowlist contains a missing path")
    return files


def read_manifest(bundle: Path, expected_digest: str, revision: str) -> dict:
    hex_digest(expected_digest)
    hex_digest(revision, 40)
    no_links(bundle / "manifest.json", bundle.parent)
    with (bundle / "manifest.json").open("rb") as handle:
        raw = handle.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST or digest(raw) != expected_digest:
        raise ValueError("manifest integrity mismatch")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    if (manifest.get("schema") != SCHEMA or manifest.get("codec") != "zlib+hex"
            or manifest.get("source_revision") != revision):
        raise ValueError("manifest schema, codec or source revision mismatch")
    for collection, max_count, size_key, max_size in (
            ("files", MAX_FILES, "data_bytes", MAX_BYTES),
            ("parts", MAX_PARTS, "packed_bytes", MAX_BYTES + 1024 * 1024)):
        rows = manifest[collection]
        if not isinstance(rows, list):
            raise ValueError("manifest collection must be a list")
        integer(len(rows), 1, max_count)
        checked_paths([r["path"] for r in rows])
        offset = 0
        for index, row in enumerate(rows):
            if integer(row["offset"], 0, max_size) != offset:
                raise ValueError("manifest gaps, overlap or reordering")
            offset += integer(row["size"], 0 if collection == "files" else 1, max_size)
            integer(offset, 0, max_size)
            hex_digest(row["sha256"])
            if collection == "files":
                hex_digest(row["git_blob_sha1"], 40)
            else:
                if (row["path"] != f"part-{index:05d}.hex"
                        or integer(row["index"], 0, MAX_PARTS - 1) != index):
                    raise ValueError("part identity mismatch")
                integer(row["size"], 1, 64 * 1024)
                if row["encoded_size"] != 2 * row["size"] + 1:
                    raise ValueError("encoded size mismatch")
                hex_digest(row["encoded_sha256"])
        if offset != integer(manifest[size_key], 0, max_size):
            raise ValueError("manifest total mismatch")
    hex_digest(manifest["data_sha256"])
    hex_digest(manifest["packed_sha256"])
    return manifest


def recover(bundle: Path, expected_digest: str, revision: str) -> tuple[dict, dict[str, bytes]]:
    manifest = read_manifest(bundle, expected_digest, revision)
    chunks = []
    for part in manifest["parts"]:
        path = bundle / part["path"]
        no_links(path, bundle.parent)
        with path.open("rb") as handle:
            encoded = handle.read(part["encoded_size"] + 1)
        if (len(encoded) != part["encoded_size"] or not encoded.endswith(b"\n")
                or digest(encoded) != part["encoded_sha256"]):
            raise ValueError("encoded part integrity mismatch")
        text = encoded[:-1].decode("ascii")
        if not re.fullmatch(r"[0-9a-f]+", text):
            raise ValueError("noncanonical hex part")
        raw = bytes.fromhex(text)
        if len(raw) != part["size"] or digest(raw) != part["sha256"]:
            raise ValueError("part integrity mismatch")
        chunks.append(raw)
    packed = b"".join(chunks)
    if len(packed) != manifest["packed_bytes"] or digest(packed) != manifest["packed_sha256"]:
        raise ValueError("complete compressed payload mismatch")
    decoder = zlib.decompressobj()
    data = decoder.decompress(packed, manifest["data_bytes"] + 1)
    if (len(data) != manifest["data_bytes"] or not decoder.eof
            or decoder.unused_data or decoder.unconsumed_tail
            or digest(data) != manifest["data_sha256"]):
        raise ValueError("expanded payload integrity or size mismatch")
    files = {}
    for row in manifest["files"]:
        raw = data[row["offset"]:row["offset"] + row["size"]]
        if digest(raw) != row["sha256"] or git_blob(raw) != row["git_blob_sha1"]:
            raise ValueError("source file integrity mismatch")
        files[row["path"]] = raw
    return manifest, files


def restore(bundle: Path, destination: Path, expected_digest: str, revision: str) -> dict:
    # Validate the complete bundle before creating any destination. A later I/O
    # failure leaves an explicitly incomplete new folder; no original is replaced.
    manifest, files = recover(bundle, expected_digest, revision)
    no_links(destination, destination.parent)
    if not destination.parent.is_dir():
        raise ValueError("destination parent must already exist")
    destination.mkdir(exist_ok=False)
    for name, raw in files.items():
        path = destination.joinpath(*name.split("/"))
        no_links(path, destination.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    for row in manifest["files"]:
        actual = destination.joinpath(*row["path"].split("/")).read_bytes()
        if digest(actual) != row["sha256"] or git_blob(actual) != row["git_blob_sha1"]:
            raise ValueError("written source readback mismatch")
    return {"status": "SOURCE_BYTES_RESTORED", "source_revision": revision,
            "manifest_sha256": expected_digest, "files": len(files),
            "source_bytes": manifest["data_bytes"], "full_brain_connected": False}


def hydrate(fragment: Path, fragment_pin: str, reference: Path, reference_pin: str,
            destination: Path, revision: str) -> dict:
    """Fill missing exact source files; never replace conflicting or existing bytes.

    A reference pinned outside the fragment authorizes every member. This permits
    a task with one missing module to transfer that module rather than re-fetch
    the entire package. It is source transport, not executable-runtime admission.
    """
    full = read_manifest(reference, reference_pin, revision)
    part, files = recover(fragment, fragment_pin, revision)
    expected = {entry["path"]: entry for entry in full["files"]}
    for row in part["files"]:
        original = expected.get(row["path"])
        if original is None or any(row[k] != original[k] for k in ("size", "sha256", "git_blob_sha1")):
            raise ValueError("fragment member is not authorized by the pinned source reference")
    no_links(destination, destination.parent)
    if destination.exists() and not destination.is_dir():
        raise ValueError("source destination is not a directory")
    # Validate every already-present reference file before adding anything.
    present = set()
    for name, row in expected.items():
        path = destination.joinpath(*name.split("/"))
        no_links(path, destination.parent)
        if path.exists():
            if not path.is_file():
                raise ValueError("source path conflicts with a directory")
            raw = path.read_bytes()
            if (len(raw) != row["size"] or digest(raw) != row["sha256"]
                    or git_blob(raw) != row["git_blob_sha1"]):
                raise ValueError("existing source differs from the pinned reference")
            present.add(name)
    destination.mkdir(parents=True, exist_ok=True)
    added, already = [], []
    for name, raw in files.items():
        if name in present:
            already.append(name)
            continue
        path = destination.joinpath(*name.split("/"))
        no_links(path, destination.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != raw:
            raise ValueError("source fragment readback mismatch")
        added.append(name)
    available = present | set(added)
    return {"status": "SOURCE_FRAGMENT_HYDRATED", "source_revision": revision,
            "reference_manifest_sha256": reference_pin, "fragment_manifest_sha256": fragment_pin,
            "added": sorted(added), "already_verified": sorted(already),
            "reference_files_present": len(available), "reference_files_expected": len(expected),
            "missing_reference_files": sorted(set(expected) - available),
            "reference_files_complete": len(available) == len(expected),
            "runtime_executed": False, "full_brain_connected": False}


def fragment_source(bundle: Path, pin: str, revision: str, output: Path) -> dict:
    """Derive independently transferable files from a verified complete bundle."""
    original, files = recover(bundle, pin, revision)
    no_links(output, output.parent)
    output.mkdir(parents=True, exist_ok=False)
    entries = []
    for index, entry in enumerate(original["files"]):
        directory = f"f{index:03d}"
        built = build({entry["path"]: files[entry["path"]]}, output / directory, revision)
        entries.append({"source_path": entry["path"], "source_bytes": entry["size"],
                        "git_blob_sha1": entry["git_blob_sha1"], "source_sha256": entry["sha256"],
                        "bundle_path": directory, "manifest_sha256": built["manifest_sha256"],
                        "packed_bytes": built["packed_bytes"], "parts": built["parts"]})
    index = {"schema": "portable-source-file-index-v1", "source_revision": revision,
             "reference_manifest_sha256": pin, "files": entries,
             "scope": "transfer missing files independently; hydrate validates against the full source reference"}
    (output / "index.json").write_bytes(canonical(index))
    (output / ".gitattributes").write_bytes(b"*.hex -text\n*.json -text\n")
    return {"status": "SOURCE_FILE_BUNDLES_BUILT", "files": len(entries),
            "parts": sum(e["parts"] for e in entries), "source_revision": revision,
            "index_sha256": digest(canonical(index)), "full_brain_connected": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    producer = sub.add_parser("build-git")
    producer.add_argument("--repository", type=Path, required=True)
    producer.add_argument("--revision", required=True)
    producer.add_argument("--include", action="append", required=True)
    producer.add_argument("--output", type=Path, required=True)
    consumer = sub.add_parser("restore")
    consumer.add_argument("--bundle", type=Path, required=True)
    consumer.add_argument("--manifest-sha256", required=True)
    consumer.add_argument("--revision", required=True)
    consumer.add_argument("--destination", type=Path, required=True)
    filler = sub.add_parser("hydrate")
    filler.add_argument("--fragment", type=Path, required=True)
    filler.add_argument("--fragment-manifest-sha256", required=True)
    filler.add_argument("--reference", type=Path, required=True)
    filler.add_argument("--reference-manifest-sha256", required=True)
    filler.add_argument("--revision", required=True)
    filler.add_argument("--destination", type=Path, required=True)
    splitter = sub.add_parser("fragment-source")
    splitter.add_argument("--bundle", type=Path, required=True)
    splitter.add_argument("--manifest-sha256", required=True)
    splitter.add_argument("--revision", required=True)
    splitter.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "build-git":
            result = build(git_files(args.repository, args.revision, args.include),
                           args.output, args.revision)
        elif args.command == "restore":
            result = restore(args.bundle, args.destination, args.manifest_sha256, args.revision)
        elif args.command == "fragment-source":
            result = fragment_source(args.bundle, args.manifest_sha256, args.revision, args.output)
        else:
            result = hydrate(args.fragment, args.fragment_manifest_sha256,
                             args.reference, args.reference_manifest_sha256,
                             args.destination, args.revision)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, zlib.error,
            subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__,
                          "reason": str(exc), "full_brain_connected": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
