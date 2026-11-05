"""Provenance, license, and privacy gate for public data exports."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .privacy import (
    PUBLISHABLE_RIGHTS,
    PolicyDecision,
    PrivacyViolation,
    check_public_output,
    has_link_component,
    has_unsafe_path_syntax,
    is_within,
    read_stable_file,
    resolve_without_missing,
)
from .privacy_scan import ScanPolicy, scan_text

PUBLIC_EXPORT_SCHEMA_VERSION = 1
_OPAQUE_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_LICENSE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{1,63}\Z")
_DISALLOWED_LICENSES = frozenset(
    {"none", "noassertion", "unknown", "unlicensed", "all-rights-reserved"}
)
REDISTRIBUTABLE_LICENSE_IDS = frozenset(
    {
        "0BSD",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "CC0-1.0",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "ISC",
        "MIT",
        "MPL-2.0",
        "ODC-By-1.0",
        "ODbL-1.0",
        "PDM-1.0",
    }
)
_PUBLIC_DOMAIN_LICENSES = frozenset({"CC0-1.0", "PDM-1.0"})
_PROVENANCE_PREFIXES = ("https://", "doi:", "urn:", "generated:")


@dataclass(frozen=True, slots=True)
class PublicExportManifest:
    schema_version: int
    artifact: str
    artifact_sha256: str
    rights: str
    license_id: str
    provenance_refs: tuple[str, ...]
    source_ids: tuple[str, ...]
    reviewer_id: str
    reviewed_at: str
    redistribution_confirmed: bool
    local_material_excluded: bool
    manifest_path: Path


def _require_exact_keys(
    table: Mapping[str, object],
    required: frozenset[str],
    *,
    context: str,
) -> None:
    keys = frozenset(table)
    missing = required - keys
    unknown = keys - required
    if missing:
        raise PrivacyViolation("export-schema", f"{context} is missing fields: {sorted(missing)}")
    if unknown:
        raise PrivacyViolation("export-schema", f"{context} has unknown fields: {sorted(unknown)}")


def _bounded_string(value: object, *, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise PrivacyViolation("export-schema", f"{field} must be a non-empty bounded string")
    if any(ord(character) < 32 for character in value):
        raise PrivacyViolation("export-schema", f"{field} contains control characters")
    return value


def _string_list(value: object, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PrivacyViolation("export-schema", f"{field} must be a non-empty array")
    result = tuple(_bounded_string(item, field=field) for item in value)
    if len(set(result)) != len(result):
        raise PrivacyViolation("export-schema", f"{field} values must be unique")
    return result


def _artifact_relative_path(value: object) -> str:
    text = _bounded_string(value, field="artifact", maximum=1024)
    path = PurePosixPath(text)
    if (
        "\\" in text
        or path.as_posix() != text
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or has_unsafe_path_syntax(Path(text))
    ):
        raise PrivacyViolation(
            "export-schema", "artifact must be a normalized repository-relative path"
        )
    if tuple(path.parts[:2]) != ("data", "public") or len(path.parts) < 3:
        raise PrivacyViolation("export-schema", "artifact must be below data/public")
    if text.endswith(".provenance.toml"):
        raise PrivacyViolation(
            "export-schema", "a provenance sidecar is not itself an export artifact"
        )
    return path.as_posix()


def _validate_timestamp(value: object) -> str:
    timestamp = _bounded_string(value, field="reviewed_at", maximum=64)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PrivacyViolation(
            "export-schema", "reviewed_at must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise PrivacyViolation("export-schema", "reviewed_at must include a UTC offset")
    return timestamp


def _metadata_scan(values: tuple[str, ...]) -> None:
    findings = scan_text(
        "\n".join(values),
        relative_path="data/public/<provenance-metadata>",
        policy=ScanPolicy(policy_category_contexts=frozenset()),
    )
    if findings:
        classes = sorted({finding.code for finding in findings})
        raise PrivacyViolation(
            "export-metadata-privacy", f"provenance metadata failed privacy classes: {classes}"
        )


def _validate_provenance_reference(reference: str) -> None:
    if reference.startswith("https://"):
        try:
            parsed = urlsplit(reference)
            port = parsed.port
        except ValueError as exc:
            raise PrivacyViolation("export-provenance", "provenance URL is malformed") from exc
        hostname = parsed.hostname
        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise PrivacyViolation("export-provenance", "provenance URL is not public-safe")
        folded = hostname.casefold()
        if folded in {"localhost", "localhost.localdomain"} or folded.endswith(".local"):
            raise PrivacyViolation("export-provenance", "local provenance hosts are disabled")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return
        if not address.is_global:
            raise PrivacyViolation("export-provenance", "private provenance addresses are disabled")
        return
    if reference.startswith("doi:"):
        if re.fullmatch(r"doi:10\.\d{4,9}/\S{1,200}", reference, re.IGNORECASE) is None:
            raise PrivacyViolation("export-provenance", "DOI provenance identifier is malformed")
        return
    if reference.startswith("urn:"):
        if re.fullmatch(r"urn:[A-Za-z0-9][A-Za-z0-9-]{1,31}:[^\s]{1,200}", reference) is None:
            raise PrivacyViolation("export-provenance", "URN provenance identifier is malformed")
        return
    if reference.startswith("generated:"):
        identifier = reference.removeprefix("generated:")
        if (
            re.fullmatch(r"[a-z0-9][a-z0-9._/-]{2,127}", identifier) is None
            or ".." in PurePosixPath(identifier).parts
            or identifier.startswith("/")
        ):
            raise PrivacyViolation("export-provenance", "generated provenance id is malformed")
        return
    raise PrivacyViolation("export-provenance", "provenance scheme is not approved")


def load_public_export_manifest(
    manifest_path: str | Path,
    *,
    repository_root: str | Path,
) -> PublicExportManifest:
    """Load a strict provenance sidecar for one public artifact."""

    repo_path = Path(repository_root)
    if has_link_component(repo_path):
        raise PrivacyViolation("link-component", "repository root contains a link component")
    repo = resolve_without_missing(repo_path)
    candidate = Path(manifest_path)
    if not candidate.is_absolute():
        candidate = repo / candidate
    decision = check_public_output(
        candidate,
        repository_root=repo,
        allowed_prefixes=("data/public",),
    )
    if not decision.allowed or decision.resolved_path is None:
        raise PrivacyViolation(decision.code, decision.reason)
    manifest = resolve_without_missing(decision.resolved_path)
    if not manifest.name.endswith(".provenance.toml"):
        raise PrivacyViolation("export-schema", "export manifest must use .provenance.toml")
    raw = read_stable_file(manifest, root=repo / "data" / "public", max_file_bytes=1_000_000)
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise PrivacyViolation("export-parse", "export manifest is not valid UTF-8 TOML") from exc

    required = frozenset(
        {
            "schema_version",
            "artifact",
            "artifact_sha256",
            "rights",
            "license_id",
            "provenance_refs",
            "source_ids",
            "reviewer_id",
            "reviewed_at",
            "redistribution_confirmed",
            "local_material_excluded",
        }
    )
    _require_exact_keys(document, required, context="public export manifest")
    if document["schema_version"] != PUBLIC_EXPORT_SCHEMA_VERSION:
        raise PrivacyViolation("export-version", "unsupported public export schema version")
    artifact = _artifact_relative_path(document["artifact"])
    digest = _bounded_string(document["artifact_sha256"], field="artifact_sha256", maximum=64)
    if _HASH_RE.fullmatch(digest) is None:
        raise PrivacyViolation("export-schema", "artifact_sha256 must be lowercase SHA-256")
    rights = _bounded_string(document["rights"], field="rights", maximum=64)
    if rights not in PUBLISHABLE_RIGHTS:
        raise PrivacyViolation("export-rights", "rights are not eligible for public redistribution")
    license_id = _bounded_string(document["license_id"], field="license_id", maximum=64)
    if (
        _LICENSE_RE.fullmatch(license_id) is None
        or license_id.casefold() in _DISALLOWED_LICENSES
        or license_id not in REDISTRIBUTABLE_LICENSE_IDS
    ):
        raise PrivacyViolation(
            "export-license", "a concrete redistributable license id is required"
        )
    if rights == "public_domain" and license_id not in _PUBLIC_DOMAIN_LICENSES:
        raise PrivacyViolation(
            "export-license", "public-domain material requires CC0-1.0 or PDM-1.0"
        )

    provenance_refs = _string_list(document["provenance_refs"], field="provenance_refs")
    if not all(reference.startswith(_PROVENANCE_PREFIXES) for reference in provenance_refs):
        raise PrivacyViolation(
            "export-provenance",
            "provenance references must use https, doi, urn, or generated identifiers",
        )
    for reference in provenance_refs:
        _validate_provenance_reference(reference)
    source_ids = _string_list(document["source_ids"], field="source_ids")
    if not all(_OPAQUE_ID_RE.fullmatch(source_id) for source_id in source_ids):
        raise PrivacyViolation("export-provenance", "source_ids must be opaque public identifiers")
    reviewer_id = _bounded_string(document["reviewer_id"], field="reviewer_id", maximum=64)
    if _OPAQUE_ID_RE.fullmatch(reviewer_id) is None:
        raise PrivacyViolation("export-schema", "reviewer_id must be an opaque identifier")
    reviewed_at = _validate_timestamp(document["reviewed_at"])
    if document["redistribution_confirmed"] is not True:
        raise PrivacyViolation("export-rights", "redistribution_confirmed must be true")
    if document["local_material_excluded"] is not True:
        raise PrivacyViolation("export-provenance", "local_material_excluded must be true")

    artifact_path = repo / Path(artifact)
    expected_manifest = artifact_path.with_name(artifact_path.name + ".provenance.toml")
    if manifest != expected_manifest:
        raise PrivacyViolation("export-provenance", "sidecar name does not match its artifact")
    _metadata_scan(provenance_refs + source_ids + (reviewer_id,))
    return PublicExportManifest(
        PUBLIC_EXPORT_SCHEMA_VERSION,
        artifact,
        digest,
        rights,
        license_id,
        provenance_refs,
        source_ids,
        reviewer_id,
        reviewed_at,
        True,
        True,
        manifest,
    )


def check_public_export(
    artifact_path: str | Path,
    *,
    manifest_path: str | Path,
    repository_root: str | Path,
) -> PolicyDecision:
    """Validate provenance, license, content hash, and privacy before publication."""

    repo_candidate = Path(repository_root)
    if has_unsafe_path_syntax(repo_candidate):
        return PolicyDecision(False, "unsafe-path-syntax", "repository root uses disabled syntax")
    if has_link_component(repo_candidate):
        return PolicyDecision(False, "link-component", "repository root contains a link component")
    try:
        repo = resolve_without_missing(repo_candidate)
    except (FileNotFoundError, RuntimeError, OSError):
        return PolicyDecision(False, "unresolvable", "repository root does not resolve safely")
    decision = check_public_output(
        artifact_path,
        repository_root=repo,
        allowed_prefixes=("data/public",),
    )
    if not decision.allowed or decision.resolved_path is None:
        return decision
    try:
        artifact = resolve_without_missing(decision.resolved_path)
        metadata = load_public_export_manifest(manifest_path, repository_root=repo)
    except (PrivacyViolation, FileNotFoundError, RuntimeError, OSError) as exc:
        code = exc.code if isinstance(exc, PrivacyViolation) else "unresolvable"
        return PolicyDecision(False, code, "public export metadata failed closed")
    expected_artifact = repo / Path(metadata.artifact)
    if artifact != expected_artifact or not is_within(artifact, repo / "data" / "public"):
        return PolicyDecision(False, "artifact-mismatch", "manifest names a different artifact")
    try:
        raw = read_stable_file(artifact, root=repo / "data" / "public")
        manifest_raw = read_stable_file(metadata.manifest_path, root=repo / "data" / "public")
    except PrivacyViolation as exc:
        return PolicyDecision(False, exc.code, "public export failed stable-file checks")
    if hashlib.sha256(raw).hexdigest() != metadata.artifact_sha256:
        return PolicyDecision(
            False, "hash-mismatch", "artifact hash does not match provenance sidecar"
        )
    try:
        artifact_text = raw.decode("utf-8")
        manifest_text = manifest_raw.decode("utf-8")
    except UnicodeDecodeError:
        return PolicyDecision(False, "non-utf8", "public exports must be UTF-8 text")
    strict_policy = ScanPolicy(policy_category_contexts=frozenset())
    findings = scan_text(artifact_text, relative_path=metadata.artifact, policy=strict_policy)
    manifest_relative = metadata.manifest_path.relative_to(repo).as_posix()
    findings += scan_text(manifest_text, relative_path=manifest_relative, policy=strict_policy)
    if findings:
        classes = sorted({finding.code for finding in findings})
        return PolicyDecision(
            False,
            "privacy-scan-failed",
            f"public export failed privacy classes: {classes}",
            artifact,
        )
    return PolicyDecision(
        True,
        "allowed",
        "public export passed provenance, license, hash, and privacy gates",
        artifact,
    )
