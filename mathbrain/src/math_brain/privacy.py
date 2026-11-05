"""Deny-by-default source, path, and local-derivation safety checks.

The trusted boundary in this module intentionally does not discover sources.  A
caller must first create a run-local TOML manifest naming every source root.  All
reads use a stable file descriptor and repeat containment and link checks so a
path swap cannot turn an approved check into an unapproved read.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

ALLOWLIST_SCHEMA_VERSION = 1
LOCAL_DERIVATION_POLICY = "local_only"
DEFAULT_MAX_FILE_BYTES = 5_000_000

ALLOWED_CONTENT_CLASSES = frozenset(
    {"pure_mathematics", "mathematical_physics", "formal_mathematics"}
)
LOCAL_ONLY_RIGHTS = frozenset({"unknown", "private", "local_analysis_only"})
PUBLISHABLE_RIGHTS = frozenset(
    {"original_public_release", "permissive_license", "public_domain"}
)

DEFAULT_DENIED_PATH_TOKENS = frozenset(
    {
        "trading",
        "finance",
        "financial",
        "options",
        "broker",
        "portfolio",
        "account",
        "client",
        "company",
        "secret",
        "credential",
        "private_key",
        "personal",
        "archive",
        "unrelated",
    }
)

DEFAULT_DENIED_SUFFIXES = frozenset(
    {".pem", ".key", ".pfx", ".p12", ".env", ".db", ".sqlite", ".sqlite3"}
)
DEFAULT_DENIED_CONTENT_TOKENS = frozenset(
    {"api_key", "access_token", "private key", "account number", "brokerage"}
)

_AUDIT_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}\Z")
_SOURCE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}\Z")
_EMPTY_FROZENSET: frozenset[str] = frozenset()
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


class PrivacyViolation(RuntimeError):
    """A fail-closed privacy decision raised at an I/O boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    code: str
    reason: str
    resolved_path: Path | None = None


@dataclass(frozen=True, slots=True)
class SourceGrant:
    id: str
    label: str
    root: Path
    rights: str
    content_class: str
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES


@dataclass(frozen=True, slots=True)
class AllowlistManifest:
    schema_version: int
    audit_id: str
    derivation_policy: str
    local_output_root: Path
    sources: tuple[SourceGrant, ...]
    manifest_path: Path
    repository_root: Path

    def source(self, source_id: str) -> SourceGrant:
        matches = [source for source in self.sources if source.id == source_id]
        if not matches:
            raise PrivacyViolation(
                "unknown-source", "source id is not present in this audit manifest"
            )
        return matches[0]


def _parts_lower(path: Path) -> tuple[str, ...]:
    return tuple(part.casefold() for part in path.parts)


def _contains_denied_token(path: Path, denied_tokens: Iterable[str]) -> str | None:
    parts = _parts_lower(path)
    for token in sorted({item.casefold() for item in denied_tokens}):
        if any(token in part for part in parts):
            return token
    return None


def resolve_without_missing(path: Path) -> Path:
    """Resolve a path and require it to exist; callers must not guess containment."""

    return path.expanduser().resolve(strict=True)


def has_unsafe_path_syntax(path: Path) -> bool:
    """Reject alternate streams and Windows device-name aliases everywhere."""

    candidate = Path(path)
    raw = os.fspath(path)
    windows_text = raw.replace("/", os.sep)
    separator = chr(92)
    namespace_prefix = separator * 2
    if windows_text.startswith(
        (namespace_prefix + "?" + separator, namespace_prefix + "." + separator)
    ):
        return True
    if candidate.drive.startswith("\\\\"):
        return True
    if candidate.drive and not candidate.is_absolute():
        return True
    parts = candidate.parts
    if candidate.anchor and parts and parts[0] == candidate.anchor:
        parts = parts[1:]
    for part in parts:
        if ":" in part or part.endswith((" ", ".")):
            return True
        stem = part.split(".", 1)[0].casefold()
        if stem in _WINDOWS_DEVICE_NAMES:
            return True
    return False


def _is_reparse_point(path: Path) -> bool:
    """Return true for symlinks and Windows reparse points, including junctions."""

    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if getattr(metadata, "st_file_attributes", 0) & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def has_link_component(path: Path) -> bool:
    """Detect symlink or junction components without following them.

    Missing trailing components are acceptable for an output path; every
    existing ancestor is still inspected.  Other metadata errors fail closed.
    """

    expanded = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    current = Path(expanded.anchor)
    for part in expanded.parts[1:]:
        current /= part
        try:
            if _is_reparse_point(current):
                return True
        except FileNotFoundError:
            return False
        except OSError:
            return True
    return False


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_home_or_ancestor(root: Path, home: Path) -> bool:
    """Reject roots whose recursion would include the user's home directory."""

    return root == home or is_within(home, root)


def _resolve_allow_root(root: Path, *, home: Path | None = None) -> Path:
    if has_unsafe_path_syntax(root):
        raise PrivacyViolation("unsafe-path-syntax", "allow root uses disabled path syntax")
    if has_link_component(root):
        raise PrivacyViolation("link-component", "allow root contains a symlink or junction")
    resolved = resolve_without_missing(root)
    if not resolved.is_dir():
        raise PrivacyViolation("invalid-allow-root", "allow root is not a directory")
    resolved_home = resolve_without_missing(home or Path.home())
    if _is_home_or_ancestor(resolved, resolved_home):
        raise PrivacyViolation(
            "home-recursion", "allow root would recursively include the home tree"
        )
    return resolved


def check_source_path(
    candidate: str | os.PathLike[str],
    *,
    allow_roots: Iterable[str | os.PathLike[str]],
    denied_tokens: Iterable[str] = DEFAULT_DENIED_PATH_TOKENS,
    denied_suffixes: Iterable[str] = DEFAULT_DENIED_SUFFIXES,
    allow_hidden: bool = False,
    allow_hardlinks: bool = False,
    home: str | os.PathLike[str] | None = None,
) -> PolicyDecision:
    """Return an explainable eligibility decision without opening the file.

    This is the low-level primitive.  Production callers should use
    :func:`check_manifest_source_path` so roots cannot be supplied ad hoc.
    """

    original = Path(candidate)
    if has_unsafe_path_syntax(original):
        return PolicyDecision(False, "unsafe-path-syntax", "path uses disabled filesystem syntax")
    if has_link_component(original):
        return PolicyDecision(
            False, "link-component", "symlink or junction components are disabled"
        )

    try:
        resolved = resolve_without_missing(original)
    except (FileNotFoundError, RuntimeError, OSError):
        return PolicyDecision(False, "unresolvable", "path cannot be resolved safely")

    roots: list[Path] = []
    rejected_root_codes: list[str] = []
    home_path = Path(home) if home is not None else None
    for root in allow_roots:
        try:
            roots.append(_resolve_allow_root(Path(root), home=home_path))
        except PrivacyViolation as exc:
            rejected_root_codes.append(exc.code)
        except (FileNotFoundError, RuntimeError, OSError):
            rejected_root_codes.append("invalid-allow-root")
    if not roots:
        if rejected_root_codes and all(code == "home-recursion" for code in rejected_root_codes):
            return PolicyDecision(
                False, "home-recursion", "allow roots may not include the home tree"
            )
        return PolicyDecision(False, "no-valid-allow-root", "no allowlisted root resolves safely")

    containing_roots = [root for root in roots if is_within(resolved, root)]
    if not containing_roots:
        return PolicyDecision(False, "outside-allowlist", "path is outside every allowlisted root")

    try:
        metadata = resolved.lstat()
    except OSError:
        return PolicyDecision(False, "unresolvable", "path metadata cannot be read safely")
    if not stat.S_ISREG(metadata.st_mode):
        return PolicyDecision(
            False, "not-regular-file", "only regular files are eligible", resolved
        )
    if not allow_hardlinks and metadata.st_nlink > 1:
        return PolicyDecision(False, "hardlink", "multiply linked files are disabled", resolved)

    denied = _contains_denied_token(resolved, denied_tokens)
    if denied is not None:
        return PolicyDecision(
            False,
            "denied-path-token",
            f"resolved path contains denied category token: {denied}",
            resolved,
        )

    suffixes = {suffix.casefold() for suffix in denied_suffixes}
    if resolved.suffix.casefold() in suffixes:
        return PolicyDecision(
            False,
            "denied-suffix",
            f"file suffix is denied: {resolved.suffix.casefold()}",
            resolved,
        )

    if not allow_hidden:
        root = max(containing_roots, key=lambda item: len(item.parts))
        relative_parts = resolved.relative_to(root).parts
        if any(part.startswith(".") for part in relative_parts):
            return PolicyDecision(False, "hidden-path", "hidden paths are disabled", resolved)

    return PolicyDecision(True, "allowed", "path passed eligibility checks", resolved)


def _require_table_keys(
    table: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = _EMPTY_FROZENSET,
    context: str,
) -> None:
    keys = frozenset(table)
    missing = required - keys
    unknown = keys - required - optional
    if missing:
        raise PrivacyViolation("manifest-schema", f"{context} is missing fields: {sorted(missing)}")
    if unknown:
        raise PrivacyViolation(
            "manifest-schema", f"{context} has unknown fields: {sorted(unknown)}"
        )


def _require_string(value: object, *, field: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise PrivacyViolation("manifest-schema", f"{field} must be a non-empty bounded string")
    if any(ord(character) < 32 for character in value):
        raise PrivacyViolation("manifest-schema", f"{field} contains control characters")
    return value


def _relative_local_root(value: object, audit_id: str) -> Path:
    text = _require_string(value, field="local_output_root")
    normalized = Path(text)
    if (
        "\\" in text
        or normalized.as_posix() != text
        or normalized.is_absolute()
        or ".." in normalized.parts
    ):
        raise PrivacyViolation(
            "manifest-schema", "local_output_root must be a repository-relative path"
        )
    expected = Path("local") / "audits" / audit_id
    if normalized != expected:
        raise PrivacyViolation(
            "manifest-schema", "local_output_root must be exactly local/audits/<audit_id>"
        )
    return expected


def _stable_fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    # Windows reports st_ctime_ns differently through a pathname and an open
    # descriptor on some filesystems.  Device/inode, mode, size, and mtime are
    # stable across both views and still detect replacement before any read.
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def read_stable_file(
    candidate: str | os.PathLike[str],
    *,
    root: str | os.PathLike[str],
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    reject_hardlinks: bool = True,
) -> bytes:
    """Read a regular file through a stable descriptor with pre/post checks.

    The descriptor identity is compared before any bytes are read.  After the
    read, the file and every path component are revalidated.  A rename, link
    insertion, replacement, or in-place modification fails closed.
    """

    if max_file_bytes <= 0:
        raise PrivacyViolation("invalid-size-limit", "maximum file size must be positive")
    original = Path(candidate)
    root_path = Path(root)
    if has_unsafe_path_syntax(original) or has_unsafe_path_syntax(root_path):
        raise PrivacyViolation("unsafe-path-syntax", "path uses disabled filesystem syntax")
    if has_link_component(original) or has_link_component(root_path):
        raise PrivacyViolation("link-component", "path contains a symlink or junction")
    try:
        resolved_root = resolve_without_missing(root_path)
        resolved = resolve_without_missing(original)
        before = resolved.lstat()
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise PrivacyViolation("unresolvable", "file cannot be resolved safely") from exc
    if not is_within(resolved, resolved_root):
        raise PrivacyViolation("outside-allowlist", "file escapes its approved root")
    if not stat.S_ISREG(before.st_mode):
        raise PrivacyViolation("not-regular-file", "only regular files may be read")
    if reject_hardlinks and before.st_nlink > 1:
        raise PrivacyViolation("hardlink", "multiply linked files are disabled")
    if before.st_size > max_file_bytes:
        raise PrivacyViolation("file-too-large", "file exceeds the manifest size limit")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise PrivacyViolation("open-failed", "approved file could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if _stable_fingerprint(opened) != _stable_fingerprint(before):
            raise PrivacyViolation("path-race", "file identity changed between validation and open")
        try:
            just_opened_root = resolve_without_missing(root_path)
            just_opened_path = resolve_without_missing(original)
            just_opened_metadata = just_opened_path.lstat()
        except (FileNotFoundError, RuntimeError, OSError) as exc:
            raise PrivacyViolation("path-race", "file path changed while it was opened") from exc
        if (
            has_link_component(original)
            or has_link_component(root_path)
            or just_opened_root != resolved_root
            or just_opened_path != resolved
            or _stable_fingerprint(just_opened_metadata) != _stable_fingerprint(opened)
        ):
            raise PrivacyViolation("path-race", "path containment changed before the read")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, max_file_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_file_bytes:
                raise PrivacyViolation("file-too-large", "file grew beyond the manifest size limit")
        after_descriptor = os.fstat(descriptor)
        if _stable_fingerprint(after_descriptor) != _stable_fingerprint(opened):
            raise PrivacyViolation("path-race", "file changed while it was being read")
    finally:
        os.close(descriptor)

    try:
        current_root = resolve_without_missing(root_path)
        current = resolve_without_missing(original)
        after_path = current.lstat()
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise PrivacyViolation("path-race", "file path changed after it was read") from exc
    if (
        current_root != resolved_root
        or current != resolved
        or has_link_component(original)
        or has_link_component(root_path)
        or _stable_fingerprint(after_path) != _stable_fingerprint(opened)
    ):
        raise PrivacyViolation("path-race", "path containment or identity changed during read")
    return b"".join(chunks)


def load_allowlist_manifest(
    manifest_path: str | os.PathLike[str],
    *,
    repository_root: str | os.PathLike[str],
    home: str | os.PathLike[str] | None = None,
) -> AllowlistManifest:
    """Load and strictly validate a run-local allowlist TOML manifest."""

    repo_path = Path(repository_root)
    if has_unsafe_path_syntax(repo_path):
        raise PrivacyViolation("unsafe-path-syntax", "repository root uses disabled path syntax")
    if has_link_component(repo_path):
        raise PrivacyViolation("link-component", "repository root contains a link component")
    try:
        repo = resolve_without_missing(repo_path)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise PrivacyViolation("unresolvable", "repository root does not resolve safely") from exc
    manifest_candidate = Path(manifest_path)
    if not manifest_candidate.is_absolute():
        manifest_candidate = repo / manifest_candidate
    if has_link_component(manifest_candidate):
        raise PrivacyViolation("link-component", "allowlist manifest contains a link component")
    try:
        manifest = resolve_without_missing(manifest_candidate)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        raise PrivacyViolation("manifest-missing", "allowlist manifest does not resolve") from exc
    if not is_within(manifest, repo / "local" / "audits"):
        raise PrivacyViolation("manifest-location", "allowlist manifest must be under local/audits")
    raw = read_stable_file(manifest, root=repo / "local" / "audits", max_file_bytes=1_000_000)
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PrivacyViolation(
            "manifest-parse", "allowlist manifest is not valid UTF-8 TOML"
        ) from exc
    _require_table_keys(
        document,
        required=frozenset(
            {
                "schema_version",
                "audit_id",
                "derivation_policy",
                "no_home_recursion",
                "local_output_root",
                "sources",
            }
        ),
        context="allowlist manifest",
    )
    if document["schema_version"] != ALLOWLIST_SCHEMA_VERSION:
        raise PrivacyViolation("manifest-version", "unsupported allowlist schema version")
    audit_id = _require_string(document["audit_id"], field="audit_id", maximum=64)
    if _AUDIT_ID_RE.fullmatch(audit_id) is None:
        raise PrivacyViolation("manifest-schema", "audit_id must be an opaque lowercase identifier")
    if document["derivation_policy"] != LOCAL_DERIVATION_POLICY:
        raise PrivacyViolation("manifest-schema", "derivation_policy must be local_only")
    if document["no_home_recursion"] is not True:
        raise PrivacyViolation("manifest-schema", "no_home_recursion must be true")
    local_relative = _relative_local_root(document["local_output_root"], audit_id)
    local_output = repo / local_relative
    expected_manifest = local_output / "allowlist.toml"
    if manifest != expected_manifest:
        raise PrivacyViolation(
            "manifest-location", "manifest path must match local_output_root/allowlist.toml"
        )
    if has_link_component(local_output):
        raise PrivacyViolation("link-component", "local audit output contains a link component")

    source_tables = document["sources"]
    if not isinstance(source_tables, list) or not source_tables:
        raise PrivacyViolation("manifest-schema", "sources must be a non-empty array of tables")
    resolved_home = Path(home) if home is not None else None
    grants: list[SourceGrant] = []
    seen_ids: set[str] = set()
    seen_roots: set[Path] = set()
    for index, item in enumerate(source_tables):
        if not isinstance(item, dict):
            raise PrivacyViolation("manifest-schema", f"sources[{index}] must be a table")
        _require_table_keys(
            item,
            required=frozenset({"id", "label", "root", "rights", "content_class"}),
            optional=frozenset({"max_file_bytes"}),
            context=f"sources[{index}]",
        )
        source_id = _require_string(item["id"], field=f"sources[{index}].id", maximum=64)
        if _SOURCE_ID_RE.fullmatch(source_id) is None or source_id in seen_ids:
            raise PrivacyViolation(
                "manifest-schema", "source ids must be unique opaque identifiers"
            )
        label = _require_string(item["label"], field=f"sources[{index}].label", maximum=128)
        rights = _require_string(item["rights"], field=f"sources[{index}].rights", maximum=64)
        if rights not in LOCAL_ONLY_RIGHTS | PUBLISHABLE_RIGHTS:
            raise PrivacyViolation("manifest-schema", "source rights value is not recognized")
        content_class = _require_string(
            item["content_class"], field=f"sources[{index}].content_class", maximum=64
        )
        if content_class not in ALLOWED_CONTENT_CLASSES:
            raise PrivacyViolation("manifest-schema", "source content_class is not approved")
        raw_root = _require_string(item["root"], field=f"sources[{index}].root", maximum=4096)
        root_path = Path(raw_root)
        if not root_path.is_absolute():
            raise PrivacyViolation("manifest-schema", "every source root must be absolute")
        root = _resolve_allow_root(root_path, home=resolved_home)
        denied = _contains_denied_token(root, DEFAULT_DENIED_PATH_TOKENS)
        if denied is not None:
            raise PrivacyViolation("denied-path-token", "source root belongs to a denied category")
        if root in seen_roots:
            raise PrivacyViolation("manifest-schema", "source roots must be unique")
        maximum = item.get("max_file_bytes", DEFAULT_MAX_FILE_BYTES)
        if (
            not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or not 1 <= maximum <= 100_000_000
        ):
            raise PrivacyViolation("manifest-schema", "max_file_bytes is outside the safe range")
        grants.append(SourceGrant(source_id, label, root, rights, content_class, maximum))
        seen_ids.add(source_id)
        seen_roots.add(root)

    return AllowlistManifest(
        ALLOWLIST_SCHEMA_VERSION,
        audit_id,
        LOCAL_DERIVATION_POLICY,
        local_output,
        tuple(grants),
        manifest,
        repo,
    )


def check_manifest_source_path(
    candidate: str | os.PathLike[str],
    *,
    manifest: AllowlistManifest,
    source_id: str,
) -> PolicyDecision:
    """Check a file against exactly one source grant in an audit manifest."""

    try:
        source = manifest.source(source_id)
    except PrivacyViolation as exc:
        return PolicyDecision(False, exc.code, str(exc))
    return check_source_path(candidate, allow_roots=[source.root])


def read_allowed_source(
    candidate: str | os.PathLike[str],
    *,
    manifest: AllowlistManifest,
    source_id: str,
) -> bytes:
    """Read an allowlisted file without exposing a check/open race."""

    source = manifest.source(source_id)
    decision = check_source_path(candidate, allow_roots=[source.root])
    if not decision.allowed or decision.resolved_path is None:
        raise PrivacyViolation(decision.code, decision.reason)
    return read_stable_file(
        decision.resolved_path,
        root=source.root,
        max_file_bytes=source.max_file_bytes,
    )


def _resolve_output_candidate(candidate: Path, repo: Path) -> Path:
    path = candidate if candidate.is_absolute() else repo / candidate
    if has_unsafe_path_syntax(path):
        raise PrivacyViolation("unsafe-path-syntax", "output uses disabled filesystem syntax")
    if has_link_component(path):
        raise PrivacyViolation("link-component", "output path contains a symlink or junction")
    parent = resolve_without_missing(path.parent)
    resolved = parent / path.name
    if path.exists():
        existing = resolve_without_missing(path)
        if existing != resolved:
            raise PrivacyViolation("link-component", "output path resolves through a link")
    return resolved


def check_local_derivation_output(
    candidate: str | os.PathLike[str],
    *,
    manifest: AllowlistManifest,
) -> PolicyDecision:
    """Require every derived local artifact to stay inside its run directory."""

    try:
        output = _resolve_output_candidate(Path(candidate), manifest.repository_root)
        root = resolve_without_missing(manifest.local_output_root)
    except (PrivacyViolation, FileNotFoundError, RuntimeError, OSError) as exc:
        code = exc.code if isinstance(exc, PrivacyViolation) else "unresolvable"
        return PolicyDecision(False, code, "local derivation path cannot be resolved safely")
    if output == root or not is_within(output, root):
        return PolicyDecision(False, "outside-local-audit", "derivation escapes local audit root")
    if output == manifest.manifest_path:
        return PolicyDecision(False, "manifest-overwrite", "the audit manifest is immutable input")
    if output.exists():
        try:
            metadata = output.lstat()
        except OSError:
            return PolicyDecision(False, "unresolvable", "existing output metadata is unavailable")
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink > 1:
            return PolicyDecision(
                False, "unsafe-output", "existing output is not a private regular file"
            )
        return PolicyDecision(False, "output-exists", "local derivations are append-only")
    return PolicyDecision(True, "allowed", "derivation remains local to this audit", output)


def write_local_derivation(
    candidate: str | os.PathLike[str],
    data: bytes,
    *,
    manifest: AllowlistManifest,
) -> Path:
    """Create, never overwrite, a run-local derivation with private permissions."""

    if not isinstance(data, bytes):
        raise PrivacyViolation("invalid-output-data", "local derivations must be immutable bytes")
    decision = check_local_derivation_output(candidate, manifest=manifest)
    if not decision.allowed or decision.resolved_path is None:
        raise PrivacyViolation(decision.code, decision.reason)
    output = decision.resolved_path
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(output, flags, 0o600)
    except OSError as exc:
        raise PrivacyViolation("output-create-failed", "local derivation was not created") from exc
    failed = False
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("local derivation write made no progress")
            view = view[written:]
        os.fsync(descriptor)
        created_fingerprint = _stable_fingerprint(os.fstat(descriptor))
    except OSError as exc:
        failed = True
        raise PrivacyViolation("output-write-failed", "local derivation was not completed") from exc
    finally:
        os.close(descriptor)
        if failed:
            output.unlink(missing_ok=True)
    try:
        current_root = resolve_without_missing(manifest.local_output_root)
        current_output = resolve_without_missing(output)
        current_fingerprint = _stable_fingerprint(current_output.lstat())
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        output.unlink(missing_ok=True)
        raise PrivacyViolation("path-race", "local output path changed during creation") from exc
    if (
        current_root != manifest.local_output_root
        or current_output != output
        or has_link_component(output)
        or current_fingerprint != created_fingerprint
    ):
        output.unlink(missing_ok=True)
        raise PrivacyViolation("path-race", "local output path changed during creation")
    return output


def check_public_output(
    candidate: str | os.PathLike[str],
    *,
    repository_root: str | os.PathLike[str],
    allowed_prefixes: Iterable[str] = ("data/public", "docs", "src", "tests", "config"),
) -> PolicyDecision:
    """Require committed output to remain inside an explicit public-safe prefix."""

    try:
        repo_path = Path(repository_root)
        if has_unsafe_path_syntax(repo_path):
            raise PrivacyViolation(
                "unsafe-path-syntax", "repository root uses disabled path syntax"
            )
        if has_link_component(repo_path):
            raise PrivacyViolation("link-component", "repository root contains a link")
        repo = resolve_without_missing(repo_path)
    except (PrivacyViolation, FileNotFoundError, RuntimeError, OSError) as exc:
        code = exc.code if isinstance(exc, PrivacyViolation) else "unresolvable"
        return PolicyDecision(False, code, "output path cannot be resolved safely")

    candidate_path = Path(candidate)
    lexical = candidate_path if candidate_path.is_absolute() else repo / candidate_path
    lexical = Path(os.path.abspath(lexical))
    if not is_within(lexical, repo):
        return PolicyDecision(False, "outside-repository", "output escapes repository", lexical)
    allowed_roots: list[Path] = []
    for prefix in allowed_prefixes:
        relative = Path(prefix.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            continue
        allowed_roots.append(repo / relative)
    if not any(is_within(lexical, root) for root in allowed_roots):
        return PolicyDecision(
            False,
            "nonpublic-prefix",
            "output is not under a public-safe prefix",
            lexical,
        )
    try:
        resolved = _resolve_output_candidate(candidate_path, repo)
    except (PrivacyViolation, FileNotFoundError, RuntimeError, OSError) as exc:
        code = exc.code if isinstance(exc, PrivacyViolation) else "unresolvable"
        return PolicyDecision(False, code, "output path cannot be resolved safely")
    if not any(is_within(resolved, root) for root in allowed_roots):
        return PolicyDecision(False, "path-race", "resolved output left its public-safe prefix")
    return PolicyDecision(True, "allowed", "output is under a public-safe prefix", resolved)
