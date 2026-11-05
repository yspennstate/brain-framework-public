#!/usr/bin/env python3
"""Safe text-document attachments for brain memory nodes.

A memory node remains the small, searchable anchor.  Longer instructions or evidence can
live beside it in ``memory/node_documents/<slug>/`` under a content-hashed manifest.  The
reader verifies the node, manifest, path confinement, byte count, SHA-256, file type, and
UTF-8 decoding before exposing any document to an agent.  A broken required attachment is
an error, never an empty string or a quietly skipped note.

The default attachment is ``auto_read``: deliberate ``brain.py recall``/``brain.py node``
loads its complete text with the parent.  Auto-read bytes are deliberately bounded so the
agent channel cannot silently keep only the head.  Larger reference files may be attached
``--on-demand`` and read in deterministic parts.

This module is stdlib-only.  ``BRAIN_MEM_DIR`` is the test/runtime seam.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import math
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SCHEMA = "brain-node-documents-v1"
BUNDLE_DIRNAME = "node_documents"
MANIFEST_NAME = "manifest.json"
WRITE_LOCK_NAME = ".brain_node_documents.write.lock"
MAX_DOCUMENTS = 32
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
# A deliberate recall is commonly returned through a roughly 10k-token tool channel.
# Keep the automatically injected portion comfortably below it, including the node/result text.
MAX_AUTO_READ_BYTES = 24 * 1024
# Wake hooks have a hard 10,000-character inline boundary.  The rest of the wake needs room too.
# Claude Part 1 leaves roughly 5K under its hard 10K inline cap. Reserve room for the
# self-care warning itself and normal identity growth: explicit always-wake documents must fit
# completely, while larger instruction sets remain available through auto-read/on-demand nodes.
MAX_WAKE_BYTES = 3 * 1024
DEFAULT_PART_CHARS = 8000
ALLOWED_SUFFIXES = frozenset((".md", ".txt"))
SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{2,120}\Z")  # 2026-08-05: corpus carries legit 82-char slugs; 80 crashed the bm25 rebuild
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}\Z")
TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")
STOP = frozenset(
    "the and for that this with are was but not you your from have has had can will would "
    "should could been being they them their there here what when where which while about "
    "into over under after before between during against more most some such only just than "
    "then also very each other same our ours its his her she him who these those does did how "
    "why all any both too own out off above below again because until unless".split()
)


class AttachmentError(RuntimeError):
    """The attachment contract is malformed, incomplete, or unsafe."""


@dataclass(frozen=True)
class NodeDocument:
    node: str
    name: str
    path: Path
    relative_path: str
    description: str
    sha256: str
    bytes: int
    required: bool
    auto_read: bool
    wake: bool
    text: str


def memory_dir(value: str | os.PathLike[str] | None = None) -> Path:
    if value is not None:
        return Path(value)
    configured = os.environ.get("BRAIN_MEM_DIR")
    if configured:
        return Path(configured)
    return Path.home() / ".claude" / "projects" / "{{BRAIN_SLUG}}" / "memory"


def _slug(value: str) -> str:
    value = value[:-3] if value.endswith(".md") else value
    if not SLUG_RE.fullmatch(value or ""):
        raise AttachmentError("invalid node slug: %r" % value)
    return value


def _name(value: str) -> str:
    if not NAME_RE.fullmatch(value or "") or Path(value).name != value:
        raise AttachmentError("document name must be one safe basename")
    if Path(value).suffix.lower() not in ALLOWED_SUFFIXES:
        raise AttachmentError("document must end in .md or .txt")
    return value


def _is_reparse(info: os.stat_result) -> bool:
    return bool(getattr(info, "st_file_attributes", 0) &
                getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _assert_regular(path: Path, label: str) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise AttachmentError("%s is missing or unreadable: %s" % (label, exc)) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or _is_reparse(info):
        raise AttachmentError("%s is not a regular non-link file: %s" % (label, path))
    return info


def _assert_directory(path: Path, label: str) -> os.stat_result:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise AttachmentError("%s is missing or unreadable: %s" % (label, exc)) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or _is_reparse(info):
        raise AttachmentError("%s is not a regular non-link directory: %s" % (label, path))
    return info


def _strict_utf8(raw: bytes, label: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AttachmentError("%s is not strict UTF-8: %s" % (label, exc)) from exc


def _read_bytes(path: Path, label: str, limit: int) -> bytes:
    info = _assert_regular(path, label)
    if info.st_size > limit:
        raise AttachmentError("%s is %d bytes; limit is %d" % (label, info.st_size, limit))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AttachmentError("cannot read %s: %s" % (label, exc)) from exc
    if len(raw) != info.st_size:
        raise AttachmentError("%s changed while it was read" % label)
    return raw


def _node_path(mem: Path, slug: str) -> Path:
    flat = mem / (slug + ".md")
    if not os.path.lexists(flat):
        # Dream journals live in memory/dreams/, not at the memory root.
        nested = mem / "dreams" / (slug + ".md")
        if os.path.lexists(nested):
            return nested
    return flat


def bundle_dir(mem: str | os.PathLike[str] | Path, slug: str) -> Path:
    return Path(mem) / BUNDLE_DIRNAME / _slug(slug)


def manifest_path(mem: str | os.PathLike[str] | Path, slug: str) -> Path:
    return bundle_dir(mem, slug) / MANIFEST_NAME


def _contained(root: Path, candidate: Path) -> bool:
    try:
        resolved_root = os.path.normcase(str(root.resolve()))
        resolved_candidate = os.path.normcase(str(candidate.resolve()))
        return os.path.commonpath((resolved_root, resolved_candidate)) == resolved_root
    except (OSError, ValueError):
        return False


def _ensure_bundle_directories(mem: Path, slug: str) -> tuple[Path, Path]:
    """Create each bundle level without traversing an existing link/reparse point."""
    store = mem / BUNDLE_DIRNAME
    if store.exists():
        _assert_directory(store, "node document store")
    else:
        os.mkdir(store, 0o700)
        _assert_directory(store, "node document store")
    root = store / slug
    if root.exists():
        _assert_directory(root, "node document directory")
    else:
        os.mkdir(root, 0o700)
        _assert_directory(root, "node document directory")
    docs_root = root / "documents"
    if docs_root.exists():
        _assert_directory(docs_root, "node documents directory")
    else:
        os.mkdir(docs_root, 0o700)
        _assert_directory(docs_root, "node documents directory")
    return root, docs_root


def _empty_manifest(slug: str) -> dict:
    return {
        "schema": SCHEMA,
        "node": slug,
        "version": 1,
        "documents": [],
    }


def _load_manifest(mem: Path, slug: str, *, missing_ok: bool = True) -> dict:
    slug = _slug(slug)
    path = manifest_path(mem, slug)
    if not os.path.lexists(path):
        root = bundle_dir(mem, slug)
        # No bundle means the node has never had attached documents.  An existing bundle with
        # no manifest is different: it is an interrupted/deleted index of real note bytes and
        # must never be flattened into the same harmless empty result.
        if os.path.lexists(root):
            _assert_directory(root, "node document directory")
            raise AttachmentError(
                "attachment manifest is missing for existing bundle %s" % slug)
        if missing_ok:
            return _empty_manifest(slug)
        raise AttachmentError("attachment manifest is missing for %s" % slug)
    raw = _read_bytes(path, "attachment manifest", 1024 * 1024)
    try:
        data = json.loads(_strict_utf8(raw, "attachment manifest"))
    except json.JSONDecodeError as exc:
        raise AttachmentError("attachment manifest is not valid JSON: %s" % exc) from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise AttachmentError("attachment manifest schema is not %s" % SCHEMA)
    if data.get("node") != slug:
        raise AttachmentError("attachment manifest node does not match %s" % slug)
    docs = data.get("documents")
    if not isinstance(docs, list) or len(docs) > MAX_DOCUMENTS:
        raise AttachmentError("attachment manifest documents must be a list of at most %d" % MAX_DOCUMENTS)
    return data


def _document_inventory(docs_root: Path) -> list[str]:
    """Return exact safe basenames present on disk; any orphan is an integrity failure."""
    try:
        children = sorted(docs_root.iterdir(), key=lambda path: (path.name.casefold(), path.name))
    except OSError as exc:
        raise AttachmentError("cannot enumerate node documents directory: %s" % exc) from exc
    names = []
    for child in children:
        _assert_regular(child, "attached document inventory entry")
        names.append(_name(child.name))
    return names


def _validate_row(row: object, slug: str, seen: set[str]) -> dict:
    if not isinstance(row, dict):
        raise AttachmentError("attachment row for %s is not an object" % slug)
    name = _name(str(row.get("name") or ""))
    if name in seen:
        raise AttachmentError("duplicate attached document name %s" % name)
    seen.add(name)
    expected_rel = "documents/" + name
    if row.get("path") != expected_rel:
        raise AttachmentError("attachment path must be exactly %s" % expected_rel)
    sha = str(row.get("sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise AttachmentError("attachment %s has an invalid SHA-256" % name)
    size = row.get("bytes")
    if not isinstance(size, int) or isinstance(size, bool) or not (0 <= size <= MAX_DOCUMENT_BYTES):
        raise AttachmentError("attachment %s has an invalid byte count" % name)
    description = row.get("description", "")
    if not isinstance(description, str) or len(description) > 240 or "\n" in description or "\r" in description:
        raise AttachmentError("attachment %s has an invalid description" % name)
    for key in ("required", "auto_read", "wake"):
        if not isinstance(row.get(key), bool):
            raise AttachmentError("attachment %s field %s must be boolean" % (name, key))
    if not row["required"]:
        raise AttachmentError(
            "attachment %s is marked optional; optional integrity is not supported" % name)
    if row["wake"] and not row["auto_read"]:
        raise AttachmentError("wake attachment %s must also be auto_read" % name)
    return row


def _load_documents_unlocked(mem: str | os.PathLike[str] | Path, slug: str,
                             *, missing_ok: bool = True) -> list[NodeDocument]:
    """Load and cryptographically verify every document attached to ``slug``."""
    mem = Path(mem)
    slug = _slug(slug)
    _assert_directory(mem, "memory directory")
    _assert_regular(_node_path(mem, slug), "parent memory node")
    data = _load_manifest(mem, slug, missing_ok=missing_ok)
    root = bundle_dir(mem, slug)
    # A truly untouched node has neither a bundle nor a manifest.  Once a bundle exists,
    # its directory, manifest, and byte inventory are one integrity unit, including the
    # valid (but unusual) case of an explicitly empty manifest.
    if not data["documents"] and not os.path.lexists(root):
        return []
    _assert_directory(root, "node document directory")
    docs_root = root / "documents"
    _assert_directory(docs_root, "node documents directory")
    seen: set[str] = set()
    validated = [_validate_row(original, slug, seen) for original in data["documents"]]
    manifest_names = sorted(
        (row["name"] for row in validated), key=lambda name: (name.casefold(), name))
    inventory = _document_inventory(docs_root)
    if inventory != manifest_names:
        manifest_set = set(manifest_names)
        inventory_set = set(inventory)
        unmanifested = sorted(inventory_set - manifest_set)
        missing = sorted(manifest_set - inventory_set)
        raise AttachmentError(
            "attachment inventory mismatch for %s (unmanifested on disk: %s; "
            "missing from disk: %s)" %
            (slug, ", ".join(unmanifested) or "none", ", ".join(missing) or "none"))
    loaded: list[NodeDocument] = []
    total = auto_total = wake_total = 0
    for row in validated:
        path = root / Path(row["path"])
        if not _contained(root, path):
            raise AttachmentError("attachment path escapes its node directory")
        raw = _read_bytes(path, "attached document %s" % row["name"], MAX_DOCUMENT_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != row["bytes"]:
            raise AttachmentError("attachment %s byte count changed (%d != %d)" %
                                  (row["name"], len(raw), row["bytes"]))
        if digest != row["sha256"]:
            raise AttachmentError("attachment %s SHA-256 mismatch" % row["name"])
        text = _strict_utf8(raw, "attached document %s" % row["name"])
        total += len(raw)
        auto_total += len(raw) if row["auto_read"] else 0
        wake_total += len(raw) if row["wake"] else 0
        loaded.append(NodeDocument(
            node=slug,
            name=row["name"],
            path=path,
            relative_path=row["path"],
            description=row["description"],
            sha256=row["sha256"],
            bytes=row["bytes"],
            required=row["required"],
            auto_read=row["auto_read"],
            wake=row["wake"],
            text=text,
        ))
    if total > MAX_TOTAL_BYTES:
        raise AttachmentError("node %s attachments exceed %d total bytes" % (slug, MAX_TOTAL_BYTES))
    if auto_total > MAX_AUTO_READ_BYTES:
        raise AttachmentError("node %s auto-read attachments exceed %d bytes" %
                              (slug, MAX_AUTO_READ_BYTES))
    if wake_total > MAX_WAKE_BYTES:
        raise AttachmentError("node %s wake attachments exceed %d bytes" %
                              (slug, MAX_WAKE_BYTES))
    return loaded


def _atomic_manifest(path: Path, data: dict) -> None:
    raw = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    tmp = path.with_name(".%s.tmp-%d-%d" % (path.name, os.getpid(), time.time_ns()))
    fd = None
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = None
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            tmp.unlink()
        except OSError:
            pass


@contextlib.contextmanager
def _attachment_write_lock(mem: Path, timeout_seconds: float = 10.0):
    """Serialize manifest read/verify/copy/publish across agents and processes.

    The lock lives at the memory-root level so first-time bundle directory creation is fenced too.
    It is a stable sidecar because the manifest itself is atomically replaced; locking that inode
    would not protect the next writer.  A link/reparse lock is rejected before any write.
    """
    path = mem / WRITE_LOCK_NAME
    bootstrap_deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, b"0")
                os.fsync(fd)
            except Exception:
                os.close(fd)
                raise
            break
        except FileExistsError:
            try:
                visible = os.lstat(path)
                if (stat.S_ISLNK(visible.st_mode) or not stat.S_ISREG(visible.st_mode)
                        or _is_reparse(visible)):
                    raise AttachmentError(
                        "attachment write lock is not a stable regular non-link file")
                # A peer may have completed O_EXCL creation but not its one-byte bootstrap write.
                # Wait for that tiny publication window instead of racing a write through its lock.
                if visible.st_size < 1:
                    if time.monotonic() >= bootstrap_deadline:
                        raise AttachmentError("attachment write lock bootstrap timed out")
                    time.sleep(0.01)
                    continue
                fd = os.open(path, os.O_RDWR)
                break
            except AttachmentError:
                raise
            except OSError as exc:
                if time.monotonic() >= bootstrap_deadline:
                    raise AttachmentError("cannot open attachment write lock: %s" % exc) from exc
                time.sleep(0.01)
        except OSError as exc:
            raise AttachmentError("cannot create attachment write lock: %s" % exc) from exc
    handle = os.fdopen(fd, "r+b", buffering=0)
    locked = False
    try:
        opened = os.fstat(handle.fileno())
        visible = os.lstat(path)
        if (stat.S_ISLNK(visible.st_mode) or not stat.S_ISREG(visible.st_mode)
                or _is_reparse(visible)
                or (opened.st_dev, opened.st_ino) != (visible.st_dev, visible.st_ino)):
            raise AttachmentError("attachment write lock is not a stable regular non-link file")
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                time.sleep(0.01)
        if not locked:
            raise AttachmentError("attachment write lock timed out")
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def load_documents(mem: str | os.PathLike[str] | Path, slug: str,
                   *, missing_ok: bool = True) -> list[NodeDocument]:
    """Load one coherent bundle snapshot, waiting out any in-progress manifest update."""
    mem_path = Path(mem)
    safe_slug = _slug(slug)
    _assert_directory(mem_path, "memory directory")
    _assert_regular(_node_path(mem_path, safe_slug), "parent memory node")
    # If no bundle existed at this observation, an empty result is a valid pre-write snapshot.
    # Avoid creating a synchronization sidecar for nodes that have never used attachments.
    if not os.path.lexists(bundle_dir(mem_path, safe_slug)):
        return _load_documents_unlocked(mem_path, safe_slug, missing_ok=missing_ok)
    if not _lock_writable(mem_path):
        return _load_documents_snapshot(mem_path, safe_slug, missing_ok=missing_ok)
    with _attachment_write_lock(mem_path):
        return _load_documents_unlocked(mem_path, safe_slug, missing_ok=missing_ok)


# 10 Oct 2026 (claude-9bb7da02): a Codex worker's sandbox lets it read the memory directory but not open the lock
# sidecar for writing. The lock loop retried that refusal for its 10 s bootstrap deadline on each of the 41 bundles,
# so every recall from a sandboxed solver timed out at its 60 s limit with no attachment read at all (w356, w370).
_LOCK_WRITABLE: dict = {}


def _lock_writable(mem: Path) -> bool:
    """False when this process is refused a write open of the lock sidecar; asked once per process and memory root."""
    key = os.path.normcase(os.path.abspath(str(mem)))
    if key not in _LOCK_WRITABLE:
        try:
            os.close(os.open(mem / WRITE_LOCK_NAME, os.O_RDWR))
            _LOCK_WRITABLE[key] = True
        except PermissionError:
            _LOCK_WRITABLE[key] = False
        except OSError:
            # missing (the lock path creates it) or anything else: keep the locked path and its own errors
            _LOCK_WRITABLE[key] = True
    return _LOCK_WRITABLE[key]


def _load_documents_snapshot(mem: Path, slug: str, *, missing_ok: bool = True,
                             tries: int = 3) -> list[NodeDocument]:
    """Read a bundle without the lock, for a reader that may not take it.

    Safe because the unlocked read verifies the whole bundle: the manifest is replaced atomically, the documents on
    disk must be exactly the manifest's names, and each document's length and SHA-256 must match its row. A read that
    races a writer's publish therefore raises rather than returning a mixed bundle; it is retried briefly (a publish
    takes milliseconds) and the last error is raised as before.
    """
    for attempt in range(tries):
        try:
            return _load_documents_unlocked(mem, slug, missing_ok=missing_ok)
        except (AttachmentError, OSError):
            if attempt == tries - 1:
                raise
            time.sleep(0.05 * (attempt + 1))
    return []


def _attach_unlocked(mem: str | os.PathLike[str] | Path, slug: str,
                     source: str | os.PathLike[str] | Path, *, name: str = "",
                     description: str = "", required: bool = True,
                     auto_read: bool = True, wake: bool = False) -> NodeDocument:
    """Copy one immutable text document into a node bundle and publish its manifest row."""
    mem = Path(mem)
    slug = _slug(slug)
    source = Path(source)
    _assert_directory(mem, "memory directory")
    _assert_regular(_node_path(mem, slug), "parent memory node")
    raw = _read_bytes(source, "attachment source", MAX_DOCUMENT_BYTES)
    _strict_utf8(raw, "attachment source")
    name = _name(name or source.name)
    if not isinstance(description, str) or len(description) > 240 or "\n" in description or "\r" in description:
        raise AttachmentError("description must be one line of at most 240 characters")
    if wake and not auto_read:
        raise AttachmentError("a wake document must be auto-read")
    if not required:
        raise AttachmentError(
            "optional attachments are not supported; attached instructions must fail closed")

    data = _load_manifest(mem, slug)
    # Verify every existing row and byte before extending a manifest.  A corrupt bundle
    # must not be made to look healthier by appending another valid document.
    existing = _load_documents_unlocked(mem, slug)
    if any(doc.name == name for doc in existing):
        raise AttachmentError("document %s is already attached to %s" % (name, slug))
    if len(existing) >= MAX_DOCUMENTS:
        raise AttachmentError("node already has the maximum %d documents" % MAX_DOCUMENTS)
    if sum(doc.bytes for doc in existing) + len(raw) > MAX_TOTAL_BYTES:
        raise AttachmentError("attachment would exceed the node total-byte limit")
    if auto_read and sum(doc.bytes for doc in existing if doc.auto_read) + len(raw) > MAX_AUTO_READ_BYTES:
        raise AttachmentError("attachment would exceed the auto-read limit; use --on-demand")
    if wake and sum(doc.bytes for doc in existing if doc.wake) + len(raw) > MAX_WAKE_BYTES:
        raise AttachmentError("attachment would exceed the wake-document limit")

    root, docs_root = _ensure_bundle_directories(mem, slug)
    destination = docs_root / name
    if not _contained(root, destination):
        raise AttachmentError("destination escaped its node directory")

    created = False
    try:
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        row = {
            "name": name,
            "path": "documents/" + name,
            "description": description,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "required": True,
            "auto_read": bool(auto_read),
            "wake": bool(wake),
        }
        data["documents"].append(row)
        data["documents"] = sorted(data["documents"], key=lambda item: item["name"])
        data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _atomic_manifest(root / MANIFEST_NAME, data)
    except Exception:
        if created:
            try:
                destination.unlink()
            except OSError:
                pass
        raise
    # Reopen through the production validator; never return an unverified write.
    return next(doc for doc in _load_documents_unlocked(mem, slug) if doc.name == name)


def attach(mem: str | os.PathLike[str] | Path, slug: str,
           source: str | os.PathLike[str] | Path, *, name: str = "",
           description: str = "", required: bool = True,
           auto_read: bool = True, wake: bool = False) -> NodeDocument:
    """Copy one immutable text document and publish one lossless serialized manifest update."""
    mem_path = Path(mem)
    _assert_directory(mem_path, "memory directory")
    with _attachment_write_lock(mem_path):
        return _attach_unlocked(
            mem_path, slug, source, name=name, description=description,
            required=required, auto_read=auto_read, wake=wake)


def _document_block(doc: NodeDocument) -> str:
    detail = (" - " + doc.description) if doc.description else ""
    return ("\n--- ATTACHED DOCUMENT: %s/%s%s "
            "[sha256:%s bytes:%d] ---\n%s\n" %
            (doc.node, doc.name, detail, doc.sha256[:16], doc.bytes, doc.text))


def render_node(mem: str | os.PathLike[str] | Path, slug: str, *,
                attachments_only: bool = False, auto_only: bool = False,
                wake_only: bool = False) -> str:
    """Render the complete parent node and selected verified documents."""
    mem = Path(mem)
    slug = _slug(slug)
    _assert_directory(mem, "memory directory")
    node_raw = _read_bytes(_node_path(mem, slug), "parent memory node", 8 * 1024 * 1024)
    node_text = _strict_utf8(node_raw, "parent memory node")
    docs = load_documents(mem, slug)
    if auto_only:
        docs = [doc for doc in docs if doc.auto_read]
    if wake_only:
        docs = [doc for doc in docs if doc.wake]
    prefix = "" if attachments_only else (
        "--- BRAIN NODE: %s [sha256:%s] ---\n%s\n" %
        (slug, hashlib.sha256(node_raw).hexdigest()[:16], node_text))
    return prefix + "".join(_document_block(doc) for doc in docs)


def render_auto_documents(mem: str | os.PathLike[str] | Path, slug: str) -> str:
    """Render only the documents a deliberate recall must place in agent context."""
    docs = [doc for doc in load_documents(mem, slug) if doc.auto_read]
    return "".join(_document_block(doc) for doc in docs)


def split_rendered(text: str, part_chars: int = DEFAULT_PART_CHARS) -> list[str]:
    if part_chars < 1000:
        raise AttachmentError("part size must be at least 1000 characters")
    return [text[i:i + part_chars] for i in range(0, len(text), part_chars)] or [""]


def render_part(mem: str | os.PathLike[str] | Path, slug: str, part: int,
                *, part_chars: int = DEFAULT_PART_CHARS,
                attachments_only: bool = False, auto_only: bool = False) -> tuple[str, int]:
    rendered = render_node(mem, slug, attachments_only=attachments_only, auto_only=auto_only)
    parts = split_rendered(rendered, part_chars)
    if part < 1 or part > len(parts):
        raise AttachmentError("part must be between 1 and %d" % len(parts))
    header = "[BRAIN NODE READ %s: part %d/%d; continue until %d/%d]\n" % (
        _slug(slug), part, len(parts), len(parts), len(parts))
    return header + parts[part - 1], len(parts)


def summaries(mem: str | os.PathLike[str] | Path, slug: str) -> list[dict]:
    return [{
        "name": doc.name,
        "description": doc.description,
        "bytes": doc.bytes,
        "sha256": doc.sha256,
        "required": doc.required,
        "auto_read": doc.auto_read,
        "wake": doc.wake,
    } for doc in load_documents(mem, slug)]


def _tokens(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall((text or "").lower()) if token not in STOP]


def iter_manifest_slugs(mem: str | os.PathLike[str] | Path) -> Iterable[str]:
    root = Path(mem) / BUNDLE_DIRNAME
    if not os.path.lexists(root):
        return ()
    _assert_directory(root, "node document store")
    rows = []
    for child in sorted(root.iterdir(), key=lambda path: path.name.lower()):
        # Yield every node-shaped bundle, not only bundles whose manifest currently exists.
        # verify_all/load_documents must get the chance to report a deleted or broken manifest.
        if SLUG_RE.fullmatch(child.name):
            rows.append(child.name)
    return tuple(rows)


_IDF_CACHE: dict = {}


def _idf_table() -> tuple[dict, float]:
    """(df table, n_docs) from the nightly BM25 index, loaded once per process; empty on failure.
    Rarity is what makes a query term evidence: without it, a 25 KB attached prompt that says
    "brain", "test" and "bank" once each out-ranks the two-line memory that is about the query."""
    if "df" not in _IDF_CACHE:
        try:
            from bm25 import BM25
            bm = BM25()
            _IDF_CACHE["df"], _IDF_CACHE["n"] = bm.df, float(bm.n)
        except Exception:
            _IDF_CACHE["df"], _IDF_CACHE["n"] = {}, 0.0
    return _IDF_CACHE["df"], _IDF_CACHE["n"]


def _idf(token: str, df: dict, n: float) -> float:
    if not n:
        return 1.0
    d = df.get(token, 0)
    return math.log(1.0 + (n - d + 0.5) / (d + 0.5))


def search_nodes(mem: str | os.PathLike[str] | Path, query: str,
                 limit: int = 10, failures: list | None = None) -> list[tuple[str, float]]:
    """Small live lexical arm over attachments, so parent retrieval need not wait for nightly BM25.

    The strength is on the scale of "query terms matched" (so brain.py's promote floor of
    ~55 percent of the terms keeps its meaning) but each term is weighted by its rarity and
    each occurrence is saturated against document length the BM25 way. Measured 2026-09-02:
    the old count-of-terms strength put the same 25 KB attached prompt at rank 1 on 7 of 40
    real owner prompts and displaced the right memory on 7 of 14 positive controls, because
    a long document mentions every common word once. A single mention in a document ten
    times the average length now counts about a fifth of a term; a focused short attachment
    that matches the rare terms still clears the floor.

    failures: pass a list and a bundle that fails verification is left out of the search, its bytes and scores counting
    for nothing, and named in the list as (slug, error type) - no text of it (28 Sep, scheduled audit account03/B02
    r68, B02-R1-ATTACHMENT-CORRUPTION-GLOBAL-RECALL-STOP-001: one broken bundle anywhere used to abort the whole search,
    and brain.py's recall with it). Without the list the first failure raises, as before, for callers that want that.
    """
    q = set(_tokens(query))
    if not q:
        return []
    need = 1 if len(q) <= 3 else 2
    df, n_docs = _idf_table()
    q_mass = sum(_idf(t, df, n_docs) for t in q) or 1.0
    corpus = []
    for slug in iter_manifest_slugs(mem):
        if failures is None:
            docs = load_documents(mem, slug)
        else:
            try:
                docs = load_documents(mem, slug)
            except Exception as exc:
                failures.append((slug, type(exc).__name__))
                continue
        tokens = _tokens("\n".join(doc.text for doc in docs))
        corpus.append((slug, tokens))
    if not corpus:
        return []
    avgdl = max(1.0, sum(len(t) for _s, t in corpus) / len(corpus))
    k1, b = 1.2, 0.75
    scored = []
    for slug, tokens in corpus:
        present = q.intersection(tokens)
        if len(present) < need:
            continue
        dl = len(tokens)
        norm = k1 * (1.0 - b + b * dl / avgdl)
        mass = 0.0
        for t in present:
            tf = tokens.count(t)
            sat = (k1 + 1.0) * tf / (tf + norm)      # 1.0 for one mention in an average-length doc
            mass += _idf(t, df, n_docs) * min(sat, 1.5)
        strength = len(q) * mass / q_mass
        if strength <= 0.0:
            continue
        scored.append((slug, float(strength)))
    return sorted(scored, key=lambda row: (-row[1], row[0]))[:max(1, limit)]


def search_text_for_node(mem: str | os.PathLike[str] | Path, slug: str) -> str:
    """Verified attachment text for a parent's BM25 document."""
    return "\n".join(doc.text for doc in load_documents(mem, slug))


def newest_attachment_mtime(mem: str | os.PathLike[str] | Path, slug: str) -> float:
    """Latest mtime over a parent's attachment files (0.0 when none or unreadable).
    brain.py compares this with the nightly BM25 index build time: only an attachment the
    index has not seen yet earns live promotion above the bm25 block."""
    newest = 0.0
    try:
        for doc in load_documents(mem, slug):
            try:
                newest = max(newest, os.stat(doc.path).st_mtime)
            except OSError:
                continue
    except Exception:
        return 0.0
    return newest


def verify_all(mem: str | os.PathLike[str] | Path) -> list[dict]:
    results = []
    for slug in iter_manifest_slugs(mem):
        try:
            docs = load_documents(mem, slug)
            results.append({"node": slug, "ok": True, "documents": len(docs),
                            "bytes": sum(doc.bytes for doc in docs)})
        except Exception as exc:
            results.append({"node": slug, "ok": False,
                            "error": "%s: %s" % (type(exc).__name__, exc)})
    return results


def render_wake_documents(mem: str | os.PathLike[str] | Path) -> str:
    blocks = []
    total = 0
    for slug in iter_manifest_slugs(mem):
        for doc in load_documents(mem, slug):
            if doc.wake:
                total += doc.bytes
                if total > MAX_WAKE_BYTES:
                    raise AttachmentError(
                        "global wake attachments exceed %d bytes" % MAX_WAKE_BYTES)
                blocks.append(_document_block(doc))
    return "".join(blocks)


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-dir", default=str(memory_dir()))
    sub = parser.add_subparsers(dest="command", required=True)
    p_attach = sub.add_parser("attach", help="copy and attach one UTF-8 .md/.txt document")
    p_attach.add_argument("slug")
    p_attach.add_argument("--file", required=True)
    p_attach.add_argument("--name", default="")
    p_attach.add_argument("--description", default="")
    p_attach.add_argument("--on-demand", action="store_true")
    p_attach.add_argument("--wake", action="store_true")
    p_read = sub.add_parser("read", help="read a node and all verified attached documents")
    p_read.add_argument("slug")
    p_read.add_argument("--part", type=int, default=0)
    p_read.add_argument("--part-chars", type=int, default=DEFAULT_PART_CHARS)
    p_read.add_argument("--attachments-only", action="store_true")
    p_read.add_argument("--auto-only", action="store_true")
    p_verify = sub.add_parser("verify", help="verify one node bundle or the whole store")
    p_verify.add_argument("slug", nargs="?")
    p_search = sub.add_parser("search", help="find parent nodes from attachment text")
    p_search.add_argument("query")
    sub.add_parser("wake", help="render all documents explicitly marked for wake")
    args = parser.parse_args(argv)
    mem = Path(args.memory_dir)
    try:
        if args.command == "attach":
            doc = attach(mem, args.slug, args.file, name=args.name,
                         description=args.description, required=True,
                         auto_read=not args.on_demand, wake=args.wake)
            print(json.dumps({"status": "ATTACHED", "node": doc.node, "name": doc.name,
                              "sha256": doc.sha256, "bytes": doc.bytes,
                              "auto_read": doc.auto_read, "wake": doc.wake}, sort_keys=True))
        elif args.command == "read":
            if args.part:
                payload, _count = render_part(
                    mem, args.slug, args.part, part_chars=args.part_chars,
                    attachments_only=args.attachments_only, auto_only=args.auto_only)
                sys.stdout.write(payload)
            else:
                sys.stdout.write(render_node(
                    mem, args.slug, attachments_only=args.attachments_only,
                    auto_only=args.auto_only))
        elif args.command == "verify":
            result = ({"node": _slug(args.slug), "ok": True,
                       "documents": summaries(mem, args.slug)} if args.slug else verify_all(mem))
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            if ((isinstance(result, list) and any(not row["ok"] for row in result)) or
                    (isinstance(result, dict) and not result.get("ok"))):
                return 2
        elif args.command == "search":
            print(json.dumps(search_nodes(mem, args.query), ensure_ascii=False))
        else:
            sys.stdout.write(render_wake_documents(mem))
        return 0
    except AttachmentError as exc:
        print("BRAIN NODE DOCUMENT ERROR: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
