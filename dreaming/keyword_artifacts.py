#!/usr/bin/env python3
"""Transactional publication and verified reads for the keyword-topic artifact set.

The topic layer is three files, not three independent facts. Publishing them one at a time
allowed a crash to expose a new nodes file with an old graph or related-subject file. The
publication contract here is:

  1. write every file plus a hash manifest into a new immutable generation directory;
  2. durably rename that complete directory into place;
  3. atomically replace one tiny pointer to select the generation;
  4. make readers bind once to that pointer and verify the files they consume.

A crash before step 3 leaves readers on the complete old generation. A crash after step 3
leaves them on the complete new generation. A corrupt pointer, manifest, or selected file is
an error, never permission to combine files or silently fall back to a legacy snapshot.
"""
import hashlib
import io
import json
import os
import re
import time
import uuid


ARTIFACT_NAMES = (
    "keyword_nodes.tsv",
    "keyword_graph_edges.tsv",
    "keyword_related.tsv",
)
GENERATION_DIR = "keyword_generations"
POINTER_NAME = "keyword_current.json"
MANIFEST_NAME = "manifest.json"
MANIFEST_SCHEMA = "keyword-artifact-generation-v1"
POINTER_SCHEMA = "keyword-artifact-pointer-v1"
_GENERATION_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactError(RuntimeError):
    """The selected artifact generation cannot be trusted as one complete snapshot."""


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def _write_durable(path, data):
    with open(path, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_dir(path):
    """Best-effort directory durability. Windows does not expose portable directory fsync."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (AttributeError, OSError):
        pass


def _inside(parent, child):
    parent = os.path.normcase(os.path.realpath(parent))
    child = os.path.normcase(os.path.realpath(child))
    try:
        return os.path.commonpath((parent, child)) == parent
    except (ValueError, OSError):
        return False


def _decode_json(raw, label):
    try:
        value = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ArtifactError("%s is not valid UTF-8 JSON: %s" % (label, exc))
    if not isinstance(value, dict):
        raise ArtifactError("%s must be a JSON object" % label)
    return value


def _artifact_bytes(artifacts):
    if set(artifacts) != set(ARTIFACT_NAMES):
        raise ArtifactError("publication requires exactly: %s" % ", ".join(ARTIFACT_NAMES))
    out = {}
    for name in ARTIFACT_NAMES:
        value = artifacts[name]
        if isinstance(value, str):
            value = value.encode("utf-8")
        if not isinstance(value, bytes):
            raise ArtifactError("%s must be text or bytes" % name)
        out[name] = value
    return out


def _verify_generation_dir(path, generation_id, manifest_sha256=None, required=None):
    manifest_path = os.path.join(path, MANIFEST_NAME)
    raw_manifest = _read_bytes(manifest_path)
    actual_manifest_sha = _sha256(raw_manifest)
    if manifest_sha256 and actual_manifest_sha != manifest_sha256:
        raise ArtifactError("manifest hash mismatch for generation %s" % generation_id)
    manifest = _decode_json(raw_manifest, "keyword generation manifest")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ArtifactError("unknown keyword generation manifest schema")
    if manifest.get("generation_id") != generation_id:
        raise ArtifactError("manifest generation id does not match its directory")
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(ARTIFACT_NAMES):
        raise ArtifactError("manifest does not describe the complete artifact set")

    names = tuple(required or ARTIFACT_NAMES)
    if any(name not in ARTIFACT_NAMES for name in names):
        raise ArtifactError("reader requested an unknown keyword artifact")
    paths = {}
    for name in names:
        meta = files.get(name)
        if not isinstance(meta, dict):
            raise ArtifactError("manifest entry missing for %s" % name)
        artifact_path = os.path.join(path, name)
        if not _inside(path, artifact_path) or not os.path.isfile(artifact_path):
            raise ArtifactError("artifact missing or escaped generation: %s" % name)
        raw = _read_bytes(artifact_path)
        if len(raw) != int(meta.get("bytes", -1)):
            raise ArtifactError("artifact length mismatch: %s" % name)
        if _sha256(raw) != meta.get("sha256"):
            raise ArtifactError("artifact hash mismatch: %s" % name)
        paths[name] = artifact_path
    return {
        "generation_id": generation_id,
        "manifest_sha256": actual_manifest_sha,
        "manifest": manifest,
        "paths": paths,
        "legacy": False,
    }


def load_generation(base_dir, required=None, allow_legacy=False):
    """Bind to one selected generation and verify every requested file before returning paths.

    ``allow_legacy`` exists only for the one-time deployment transition before the first pointer
    is created. It applies when the pointer is absent, never when a pointer or generation is
    corrupt; corruption must stay loud.
    """
    base_dir = os.path.abspath(base_dir)
    pointer_path = os.path.join(base_dir, POINTER_NAME)
    if not os.path.exists(pointer_path):
        if not allow_legacy:
            raise ArtifactError("keyword generation pointer is absent")
        names = tuple(required or ARTIFACT_NAMES)
        paths = {name: os.path.join(base_dir, name) for name in names}
        if any(name not in ARTIFACT_NAMES for name in names):
            raise ArtifactError("reader requested an unknown keyword artifact")
        if any(not os.path.isfile(path) for path in paths.values()):
            raise ArtifactError("keyword pointer and one or more legacy artifacts are absent")
        return {
            "generation_id": "legacy",
            "manifest_sha256": "",
            "manifest": {"schema": "legacy-unbound"},
            "paths": paths,
            "legacy": True,
        }

    pointer_raw = _read_bytes(pointer_path)
    pointer = _decode_json(pointer_raw, "keyword generation pointer")
    if pointer.get("schema") != POINTER_SCHEMA:
        raise ArtifactError("unknown keyword generation pointer schema")
    generation_id = pointer.get("generation_id", "")
    if not isinstance(generation_id, str) or not _GENERATION_RE.match(generation_id):
        raise ArtifactError("invalid keyword generation id")
    manifest_sha256 = pointer.get("manifest_sha256", "")
    if not isinstance(manifest_sha256, str) or not _GENERATION_RE.match(manifest_sha256):
        raise ArtifactError("invalid keyword manifest hash in pointer")

    root = os.path.join(base_dir, GENERATION_DIR)
    generation_path = os.path.join(root, generation_id)
    if not _inside(root, generation_path):
        raise ArtifactError("keyword generation escaped its root")
    return _verify_generation_dir(
        generation_path,
        generation_id,
        manifest_sha256=manifest_sha256,
        required=required,
    )


def publish_generation(base_dir, artifacts, source=None, fault_at=None):
    """Publish one immutable generation and atomically select it.

    ``fault_at`` is a regression-test seam. Supported values are ``after:<filename>``,
    ``after_manifest``, ``after_generation``, and ``after_pointer``. Production callers leave
    it as ``None``.
    """
    base_dir = os.path.abspath(base_dir)
    raw_files = _artifact_bytes(artifacts)
    source = source if isinstance(source, dict) else {}
    file_meta = {
        name: {"bytes": len(raw_files[name]), "sha256": _sha256(raw_files[name])}
        for name in ARTIFACT_NAMES
    }
    identity = {"files": file_meta, "source": source}
    generation_id = _sha256(_canonical_json(identity))
    root = os.path.join(base_dir, GENERATION_DIR)
    final_dir = os.path.join(root, generation_id)
    os.makedirs(root, exist_ok=True)

    if os.path.isdir(final_dir):
        selected = _verify_generation_dir(final_dir, generation_id)
        manifest_raw = _read_bytes(os.path.join(final_dir, MANIFEST_NAME))
    else:
        stage = os.path.join(root, ".tmp-%d-%s" % (os.getpid(), uuid.uuid4().hex))
        os.mkdir(stage)
        for name in ARTIFACT_NAMES:
            _write_durable(os.path.join(stage, name), raw_files[name])
            if fault_at == "after:" + name:
                raise RuntimeError("injected publication fault after %s" % name)
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "generation_id": generation_id,
            "created_at": int(time.time()),
            "files": file_meta,
            "source": source,
        }
        manifest_raw = _canonical_json(manifest) + b"\n"
        _write_durable(os.path.join(stage, MANIFEST_NAME), manifest_raw)
        if fault_at == "after_manifest":
            raise RuntimeError("injected publication fault after manifest")
        _fsync_dir(stage)
        os.replace(stage, final_dir)
        _fsync_dir(root)
        selected = _verify_generation_dir(final_dir, generation_id)

    if fault_at == "after_generation":
        raise RuntimeError("injected publication fault after generation promotion")

    pointer = {
        "schema": POINTER_SCHEMA,
        "generation_id": generation_id,
        "manifest_sha256": _sha256(manifest_raw),
    }
    pointer_path = os.path.join(base_dir, POINTER_NAME)
    pointer_tmp = pointer_path + ".tmp-%d-%s" % (os.getpid(), uuid.uuid4().hex)
    _write_durable(pointer_tmp, _canonical_json(pointer) + b"\n")
    os.replace(pointer_tmp, pointer_path)
    _fsync_dir(base_dir)
    if fault_at == "after_pointer":
        raise RuntimeError("injected publication fault after pointer promotion")
    return selected
