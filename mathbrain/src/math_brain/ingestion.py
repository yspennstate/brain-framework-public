"""Fail-closed, atomic ingestion of typed mathematical knowledge bundles."""

from __future__ import annotations

import hashlib
import hmac
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .models import (
    ClaimKind,
    EdgeKind,
    GraphEdge,
    GraphNode,
    NodeKind,
    RecordStatus,
    canonical_json,
    content_hash,
    utc_now,
)
from .overlay import (
    DIGEST_RE,
    IDENTIFIER_RE,
    ArtifactReceipt,
    ContentAddressedTextStore,
    MathNodeRevision,
)
from .store import ClaimBatchItem, ClaimBatchManifest, ResearchStore

BUNDLE_SCHEMA_VERSION = 1
MAX_BUNDLE_ENTRIES = 4_096
MAX_BUNDLE_RELATIONS = 16_384
MAX_BUNDLE_TEXT_BYTES = 32 * 1024 * 1024
MAX_EXISTING_NODE_ID_LENGTH = 512

# Evidence, verification, mistake, trigger, and supersession links have dedicated
# append paths. A knowledge manifest may only assert mathematical/discovery links.
ALLOWED_RELATION_KINDS = frozenset(
    {
        EdgeKind.DEPENDS_ON,
        EdgeKind.DEFINES,
        EdgeKind.PROVES,
        EdgeKind.REFUTES,
        EdgeKind.GENERALIZES,
        EdgeKind.SPECIALIZES,
        EdgeKind.ANALOGOUS_TO,
        EdgeKind.CITES,
        EdgeKind.SEMANTIC_ASSOCIATION,
        EdgeKind.BRIDGES_TO,
    }
)
CLAIM_TO_CLAIM_RELATIONS = ALLOWED_RELATION_KINDS - {
    EdgeKind.CITES,
    EdgeKind.BRIDGES_TO,
}
ACYCLIC_EDGE_KINDS = frozenset({EdgeKind.DEPENDS_ON, EdgeKind.SUPERSEDES})
PROOF_REQUIRED_KINDS = frozenset(
    {ClaimKind.LEMMA, ClaimKind.THEOREM, ClaimKind.PROOF_ATTEMPT}
)
RESERVED_METADATA_KEYS = frozenset(
    {"bundle_id", "bundle_digest", "bundle_entry_key", "release_manifest_digest"}
)
AUTHORIZATION_MAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")


def text_digest(value: str) -> str:
    """Hash exact UTF-8 text used by statement/proof slice bindings."""

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_digest(value: str, label: str) -> None:
    if not DIGEST_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class SourceExcerptBinding:
    """Hash and locator of one exact statement or proof slice in a stable source."""

    digest: str
    locator: str

    def __post_init__(self) -> None:
        _require_digest(self.digest, "source excerpt digest")
        if not self.locator.strip():
            raise ValueError("source excerpt locator is required")

    def canonical_payload(self) -> dict[str, str]:
        return {"digest": self.digest, "locator": self.locator}


@dataclass(frozen=True, slots=True)
class SourceArtifactBinding:
    """One stable-read source plus the exact transitive inputs it imports."""

    artifact_id: str
    source_id: str
    source_revision: str
    locator: str
    content_digest: str
    excerpts: tuple[SourceExcerptBinding, ...]
    dependency_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not IDENTIFIER_RE.fullmatch(self.artifact_id):
            raise ValueError("source artifact id must be a normalized identifier")
        for label, value in (
            ("source_id", self.source_id),
            ("source_revision", self.source_revision),
            ("locator", self.locator),
        ):
            if not value.strip():
                raise ValueError(f"source artifact {label} is required")
        _require_digest(self.content_digest, "source content digest")
        if not isinstance(self.excerpts, tuple) or not isinstance(
            self.dependency_ids, tuple
        ):
            raise TypeError("source excerpts and dependencies must be immutable tuples")
        if any(not isinstance(excerpt, SourceExcerptBinding) for excerpt in self.excerpts):
            raise TypeError("source excerpts must be SourceExcerptBinding records")
        if any(not IDENTIFIER_RE.fullmatch(item) for item in self.dependency_ids):
            raise ValueError("source dependency ids must be normalized identifiers")
        if len(set(self.dependency_ids)) != len(self.dependency_ids):
            raise ValueError("source dependency ids must not contain duplicates")
        excerpt_keys = tuple((item.digest, item.locator) for item in self.excerpts)
        if len(set(excerpt_keys)) != len(excerpt_keys):
            raise ValueError("source excerpts must not contain duplicates")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "locator": self.locator,
            "content_digest": self.content_digest,
            "excerpts": sorted(
                (excerpt.canonical_payload() for excerpt in self.excerpts),
                key=canonical_json,
            ),
            "dependency_ids": sorted(self.dependency_ids),
        }


@dataclass(frozen=True, slots=True)
class TransitiveSourceManifest:
    """Closed source graph emitted only after allowlisted stable reads."""

    allowlist_manifest_digest: str
    artifacts: tuple[SourceArtifactBinding, ...]

    def __post_init__(self) -> None:
        _require_digest(self.allowlist_manifest_digest, "allowlist manifest digest")
        if not isinstance(self.artifacts, tuple) or not self.artifacts:
            raise ValueError("transitive source manifest requires an immutable artifact tuple")
        if any(not isinstance(item, SourceArtifactBinding) for item in self.artifacts):
            raise TypeError("source manifest artifacts must be SourceArtifactBinding records")
        artifact_ids = tuple(item.artifact_id for item in self.artifacts)
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("source manifest artifact ids must be unique")
        known = set(artifact_ids)
        for artifact in self.artifacts:
            missing = set(artifact.dependency_ids) - known
            if missing:
                raise ValueError("source manifest contains an unknown transitive dependency")
            if artifact.artifact_id in artifact.dependency_ids:
                raise ValueError("source artifact cannot depend on itself")

        adjacency = {item.artifact_id: item.dependency_ids for item in self.artifacts}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(artifact_id: str) -> None:
            if artifact_id in visiting:
                raise ValueError("transitive source manifest must be acyclic")
            if artifact_id in visited:
                return
            visiting.add(artifact_id)
            for dependency_id in adjacency[artifact_id]:
                visit(dependency_id)
            visiting.remove(artifact_id)
            visited.add(artifact_id)

        for artifact_id in artifact_ids:
            visit(artifact_id)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "allowlist_manifest_digest": self.allowlist_manifest_digest,
            "artifacts": sorted(
                (artifact.canonical_payload() for artifact in self.artifacts),
                key=lambda item: item["artifact_id"],
            ),
        }

    @property
    def manifest_digest(self) -> str:
        return content_hash(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class IngestionCoreBinding:
    """Exact executable/configuration pins for one ingestion release."""

    allowlist_manifest_digest: str
    builder_digest: str
    proofswarm_parser_digest: str
    math_brain_package_digest: str
    toolchain_digest: str
    normalization_digest: str

    def __post_init__(self) -> None:
        for label, value in (
            ("allowlist manifest digest", self.allowlist_manifest_digest),
            ("builder digest", self.builder_digest),
            ("ProofSwarm parser digest", self.proofswarm_parser_digest),
            ("MathBrain package digest", self.math_brain_package_digest),
            ("toolchain digest", self.toolchain_digest),
            ("normalization digest", self.normalization_digest),
        ):
            _require_digest(value, label)

    def canonical_payload(self) -> dict[str, str]:
        return {
            "allowlist_manifest_digest": self.allowlist_manifest_digest,
            "builder_digest": self.builder_digest,
            "proofswarm_parser_digest": self.proofswarm_parser_digest,
            "math_brain_package_digest": self.math_brain_package_digest,
            "toolchain_digest": self.toolchain_digest,
            "normalization_digest": self.normalization_digest,
        }


@dataclass(frozen=True, slots=True)
class ClaimSourceBinding:
    """Exact source slices from which one bundle entry was normalized."""

    entry_key: str
    source_artifact_ids: tuple[str, ...]
    statement_digest: str
    statement_locator: str
    proof_digest: str | None = None
    proof_locator: str | None = None

    def __post_init__(self) -> None:
        if not IDENTIFIER_RE.fullmatch(self.entry_key):
            raise ValueError("claim-source entry key must be normalized")
        if not isinstance(self.source_artifact_ids, tuple) or not self.source_artifact_ids:
            raise ValueError("claim-source binding requires immutable source artifact ids")
        if any(not IDENTIFIER_RE.fullmatch(item) for item in self.source_artifact_ids):
            raise ValueError("claim-source artifact ids must be normalized")
        if len(set(self.source_artifact_ids)) != len(self.source_artifact_ids):
            raise ValueError("claim-source artifact ids must not contain duplicates")
        _require_digest(self.statement_digest, "statement digest")
        if not self.statement_locator.strip():
            raise ValueError("statement locator is required")
        if (self.proof_digest is None) != (self.proof_locator is None):
            raise ValueError("proof digest and locator must be supplied together")
        if self.proof_digest is not None:
            _require_digest(self.proof_digest, "proof digest")
            if not self.proof_locator or not self.proof_locator.strip():
                raise ValueError("proof locator is required with a proof digest")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "entry_key": self.entry_key,
            "source_artifact_ids": sorted(self.source_artifact_ids),
            "statement_digest": self.statement_digest,
            "statement_locator": self.statement_locator,
            "proof_digest": self.proof_digest,
            "proof_locator": self.proof_locator,
        }


@dataclass(frozen=True, slots=True)
class StoreBuildSeal:
    schema_digest: str
    state_digest: str

    def __post_init__(self) -> None:
        _require_digest(self.schema_digest, "store schema digest")
        _require_digest(self.state_digest, "store state digest")

    def canonical_payload(self) -> dict[str, str]:
        return {"schema_digest": self.schema_digest, "state_digest": self.state_digest}


class ReleaseDisposition(StrEnum):
    DARK = "dark"
    APPROVED = "approved"


@dataclass(frozen=True, slots=True)
class BundleReleaseManifest:
    """Cross-bound gate binding sources, executable core, review, and DB pre-state."""

    release_id: str
    disposition: ReleaseDisposition
    disposition_reason: str
    sources: TransitiveSourceManifest
    core: IngestionCoreBinding
    claim_bindings: tuple[ClaimSourceBinding, ...]
    store_seal: StoreBuildSeal
    approved_by: str = ""
    review_receipt_digest: str | None = None
    retires_release_digests: tuple[str, ...] = ()
    authorization_key_id: str = ""
    authorization_mac: str | None = None

    def __post_init__(self) -> None:
        if not IDENTIFIER_RE.fullmatch(self.release_id):
            raise ValueError("release id must be a normalized identifier")
        if not isinstance(self.disposition, ReleaseDisposition):
            raise TypeError("release disposition must be a ReleaseDisposition")
        if not self.disposition_reason.strip():
            raise ValueError("release disposition reason is required")
        if not isinstance(self.sources, TransitiveSourceManifest):
            raise TypeError("release sources must be a TransitiveSourceManifest")
        if not isinstance(self.core, IngestionCoreBinding):
            raise TypeError("release core must be an IngestionCoreBinding")
        if not isinstance(self.store_seal, StoreBuildSeal):
            raise TypeError("release store seal must be a StoreBuildSeal")
        if self.core.allowlist_manifest_digest != self.sources.allowlist_manifest_digest:
            raise ValueError("release core and source manifest bind different allowlists")
        if not isinstance(self.claim_bindings, tuple) or not self.claim_bindings:
            raise ValueError("release requires immutable claim-source bindings")
        if any(not isinstance(item, ClaimSourceBinding) for item in self.claim_bindings):
            raise TypeError("release claim bindings must be ClaimSourceBinding records")
        binding_keys = tuple(item.entry_key for item in self.claim_bindings)
        if len(set(binding_keys)) != len(binding_keys):
            raise ValueError("release claim-source entry keys must be unique")
        for digest in self.retires_release_digests:
            _require_digest(digest, "retired release digest")
        if len(set(self.retires_release_digests)) != len(self.retires_release_digests):
            raise ValueError("retired release digests must not contain duplicates")
        if self.disposition is ReleaseDisposition.APPROVED:
            if (
                not self.approved_by.strip()
                or self.review_receipt_digest is None
                or not IDENTIFIER_RE.fullmatch(self.authorization_key_id)
                or self.authorization_mac is None
                or not AUTHORIZATION_MAC_RE.fullmatch(self.authorization_mac)
            ):
                raise ValueError(
                    "approved release requires reviewer, review receipt, and authorization MAC"
                )
            _require_digest(self.review_receipt_digest, "review receipt digest")
        else:
            if self.review_receipt_digest is not None:
                _require_digest(self.review_receipt_digest, "dark review receipt digest")
            if self.authorization_key_id or self.authorization_mac is not None:
                raise ValueError("dark release must not carry an authorization receipt")

    def authorization_payload(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "disposition": self.disposition.value,
            "disposition_reason": self.disposition_reason,
            "sources": self.sources.canonical_payload(),
            "source_manifest_digest": self.sources.manifest_digest,
            "core": self.core.canonical_payload(),
            "claim_bindings": sorted(
                (item.canonical_payload() for item in self.claim_bindings),
                key=lambda item: item["entry_key"],
            ),
            "store_seal": self.store_seal.canonical_payload(),
            "approved_by": self.approved_by,
            "review_receipt_digest": self.review_receipt_digest,
            "retires_release_digests": sorted(self.retires_release_digests),
            "authorization_key_id": self.authorization_key_id,
        }

    def canonical_payload(self) -> dict[str, Any]:
        return {**self.authorization_payload(), "authorization_mac": self.authorization_mac}

    @property
    def release_digest(self) -> str:
        return content_hash(self.canonical_payload())


def release_authorization_mac(
    manifest: BundleReleaseManifest,
    key: bytes,
) -> str:
    """Create a receipt MAC; signing keys belong only to the independent gate."""

    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError("release authorization key must contain at least 32 bytes")
    payload = canonical_json(manifest.authorization_payload()).encode("utf-8")
    return "hmac-sha256:" + hmac.new(key, payload, hashlib.sha256).hexdigest()


class ReleaseVerifier:
    """Runtime verifier configured outside bundle-author-controlled data."""

    __slots__ = ("_keys",)

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        if not keys:
            raise ValueError("release verifier requires at least one trusted key")
        copied: dict[str, bytes] = {}
        for key_id, key in keys.items():
            if not IDENTIFIER_RE.fullmatch(key_id):
                raise ValueError("release verifier key ids must be normalized")
            if not isinstance(key, bytes) or len(key) < 32:
                raise ValueError("release verifier keys must contain at least 32 bytes")
            copied[key_id] = bytes(key)
        self._keys = copied

    def verify(self, manifest: BundleReleaseManifest) -> bool:
        if (
            not isinstance(manifest, BundleReleaseManifest)
            or manifest.disposition is not ReleaseDisposition.APPROVED
            or manifest.authorization_mac is None
        ):
            return False
        key = self._keys.get(manifest.authorization_key_id)
        if key is None:
            return False
        expected = release_authorization_mac(manifest, key)
        return hmac.compare_digest(expected, manifest.authorization_mac)


class EndpointScope(StrEnum):
    """Whether a relation endpoint names this bundle or an existing store node."""

    BUNDLE_ENTRY = "bundle_entry"
    EXISTING_NODE = "existing_node"


@dataclass(frozen=True, slots=True)
class BundleEndpoint:
    value: str
    scope: EndpointScope = EndpointScope.BUNDLE_ENTRY

    def __post_init__(self) -> None:
        if not isinstance(self.scope, EndpointScope):
            raise TypeError("endpoint scope must be an EndpointScope")
        if self.scope is EndpointScope.BUNDLE_ENTRY:
            if not IDENTIFIER_RE.fullmatch(self.value):
                raise ValueError("bundle endpoint must be a normalized entry key")
            return
        if (
            not self.value.strip()
            or len(self.value) > MAX_EXISTING_NODE_ID_LENGTH
            or any(ord(character) < 32 for character in self.value)
        ):
            raise ValueError("existing-node endpoint must be a bounded nonempty node id")

    @classmethod
    def entry(cls, key: str) -> BundleEndpoint:
        return cls(key, EndpointScope.BUNDLE_ENTRY)

    @classmethod
    def existing(cls, node_id: str) -> BundleEndpoint:
        return cls(node_id, EndpointScope.EXISTING_NODE)

    def canonical_payload(self) -> dict[str, str]:
        return {"scope": self.scope.value, "value": self.value}


@dataclass(frozen=True, slots=True)
class MathRelationSpec:
    """A typed relation whose bundle-local endpoints resolve at ingestion time."""

    source: BundleEndpoint
    target: BundleEndpoint
    kind: EdgeKind
    rationale: str
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.source, BundleEndpoint) or not isinstance(
            self.target, BundleEndpoint
        ):
            raise TypeError("relation endpoints must be BundleEndpoint records")
        if not isinstance(self.kind, EdgeKind):
            raise TypeError("relation kind must be an EdgeKind")
        if self.kind not in ALLOWED_RELATION_KINDS:
            raise ValueError(
                f"{self.kind.value} is not a bundle relation; use its dedicated append path"
            )
        if self.source == self.target:
            raise ValueError("a relation cannot point to itself")
        if not self.rationale.strip():
            raise ValueError("relation rationale is required")
        if not math.isfinite(self.weight) or not 0 < self.weight <= 1:
            raise ValueError("relation weight must be finite and in (0, 1]")
        canonical_json(self.metadata)
        if RESERVED_METADATA_KEYS & set(self.metadata):
            raise ValueError("relation metadata uses reserved ingestion keys")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "source": self.source.canonical_payload(),
            "target": self.target.canonical_payload(),
            "kind": self.kind.value,
            "rationale": self.rationale,
            "weight": self.weight,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class MathBundleEntry:
    key: str
    revision: MathNodeRevision

    def __post_init__(self) -> None:
        if not IDENTIFIER_RE.fullmatch(self.key):
            raise ValueError("bundle entry key must be a normalized lowercase identifier")
        if not isinstance(self.revision, MathNodeRevision):
            raise TypeError("bundle entry revision must be a MathNodeRevision")


@dataclass(frozen=True, slots=True)
class MathKnowledgeBundle:
    """An order-independent manifest for one atomic overlay append."""

    bundle_id: str
    entries: tuple[MathBundleEntry, ...]
    release_manifest: BundleReleaseManifest | None = None
    relations: tuple[MathRelationSpec, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = BUNDLE_SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not IDENTIFIER_RE.fullmatch(self.bundle_id):
            raise ValueError("bundle_id must be a normalized lowercase identifier")
        if self.schema_version != BUNDLE_SCHEMA_VERSION:
            raise ValueError(f"unsupported bundle schema version: {self.schema_version}")
        if not isinstance(self.entries, tuple) or not isinstance(self.relations, tuple):
            raise TypeError("bundle entries and relations must be immutable tuples")
        if not self.entries:
            raise ValueError("bundle must contain at least one entry")
        if len(self.entries) > MAX_BUNDLE_ENTRIES:
            raise ValueError(f"bundle exceeds the {MAX_BUNDLE_ENTRIES}-entry limit")
        if len(self.relations) > MAX_BUNDLE_RELATIONS:
            raise ValueError(f"bundle exceeds the {MAX_BUNDLE_RELATIONS}-relation limit")
        if any(not isinstance(entry, MathBundleEntry) for entry in self.entries):
            raise TypeError("bundle entries must be MathBundleEntry records")
        if any(entry.revision.status is not RecordStatus.UNVERIFIED for entry in self.entries):
            raise ValueError(
                "bundle ingestion creates unverified revisions only; "
                "use the authenticated promotion path for status changes"
            )
        if any(not isinstance(relation, MathRelationSpec) for relation in self.relations):
            raise TypeError("bundle relations must be MathRelationSpec records")
        if self.release_manifest is not None and not isinstance(
            self.release_manifest, BundleReleaseManifest
        ):
            raise TypeError("release_manifest must be a BundleReleaseManifest")
        if not self.created_at.strip():
            raise ValueError("bundle created_at is required")
        canonical_json(self.metadata)

        keys = tuple(entry.key for entry in self.entries)
        revision_ids = tuple(entry.revision.revision_id for entry in self.entries)
        logical_ids = tuple(entry.revision.logical_id for entry in self.entries)
        for label, values in (
            ("entry keys", keys),
            ("revision ids", revision_ids),
            ("logical ids", logical_ids),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"bundle must not contain duplicate {label}")

        key_set = set(keys)
        relation_keys: set[tuple[BundleEndpoint, BundleEndpoint, EdgeKind]] = set()
        for relation in self.relations:
            if relation.source.scope is not EndpointScope.BUNDLE_ENTRY:
                raise ValueError("bundle relations must originate at a bundle entry")
            for endpoint in (relation.source, relation.target):
                if (
                    endpoint.scope is EndpointScope.BUNDLE_ENTRY
                    and endpoint.value not in key_set
                ):
                    raise ValueError(f"relation references unknown bundle key: {endpoint.value}")
            relation_key = (relation.source, relation.target, relation.kind)
            if relation_key in relation_keys:
                raise ValueError("bundle must not contain duplicate typed relations")
            relation_keys.add(relation_key)

        text_bytes = sum(
            len(entry.revision.render_markdown().encode("utf-8")) for entry in self.entries
        )
        if text_bytes > MAX_BUNDLE_TEXT_BYTES:
            raise ValueError(
                f"bundle rendered text exceeds the {MAX_BUNDLE_TEXT_BYTES}-byte limit"
            )
        if self.release_manifest is not None:
            self._validate_release_manifest(self.release_manifest, key_set)
        canonical_json(self.canonical_payload())

    def _validate_release_manifest(
        self,
        manifest: BundleReleaseManifest,
        entry_keys: set[str],
    ) -> None:
        bindings = {item.entry_key: item for item in manifest.claim_bindings}
        if set(bindings) != entry_keys:
            raise ValueError("release manifest must bind every bundle entry exactly once")
        artifacts = {item.artifact_id: item for item in manifest.sources.artifacts}
        cited_roots: set[str] = set()

        for entry in self.entries:
            revision = entry.revision
            binding = bindings[entry.key]
            if binding.statement_digest != text_digest(revision.statement):
                raise ValueError("release statement digest does not match exact revision text")
            if binding.statement_locator != revision.provenance.locator:
                raise ValueError("release statement locator does not match revision provenance")
            if revision.kind in PROOF_REQUIRED_KINDS and binding.proof_digest is None:
                raise ValueError(f"{revision.kind.value} release requires proof digest and locator")

            unknown_sources = set(binding.source_artifact_ids) - set(artifacts)
            if unknown_sources:
                raise ValueError("claim-source binding references an unknown source artifact")
            selected = [artifacts[item] for item in binding.source_artifact_ids]
            cited_roots.update(binding.source_artifact_ids)
            source_digest = revision.provenance.source_content_digest
            if source_digest is None or not any(
                artifact.source_id == revision.provenance.source_id
                and artifact.source_revision == revision.provenance.source_revision
                and artifact.content_digest == source_digest
                for artifact in selected
            ):
                raise ValueError(
                    "revision provenance is not hash-bound to a selected stable source"
                )

            exact_slices = {
                (excerpt.digest, excerpt.locator)
                for artifact in selected
                for excerpt in artifact.excerpts
            }
            if (binding.statement_digest, binding.statement_locator) not in exact_slices:
                raise ValueError("statement digest and locator are absent from source excerpts")
            if binding.proof_digest is not None and (
                binding.proof_digest,
                binding.proof_locator,
            ) not in exact_slices:
                raise ValueError("proof digest and locator are absent from source excerpts")

            if revision.metadata.keys() & RESERVED_METADATA_KEYS:
                raise ValueError("revision metadata uses reserved ingestion keys")

        reached: set[str] = set()
        frontier = list(cited_roots)
        while frontier:
            artifact_id = frontier.pop()
            if artifact_id in reached:
                continue
            reached.add(artifact_id)
            frontier.extend(artifacts[artifact_id].dependency_ids)
        if reached != set(artifacts):
            raise ValueError("source manifest contains artifacts outside the cited closure")

        if any(entry.revision.supersedes for entry in self.entries) and not (
            manifest.retires_release_digests
        ):
            raise ValueError("superseding bundle requires explicit retired release digests")

    def canonical_payload(self) -> dict[str, Any]:
        entries = sorted(
            (
                {
                    "key": entry.key,
                    "logical_id": entry.revision.logical_id,
                    "revision_id": entry.revision.revision_id,
                    "revision_digest": entry.revision.revision_digest,
                    "revision": entry.revision.canonical_payload(),
                }
                for entry in self.entries
            ),
            key=lambda item: item["key"],
        )
        relations = sorted(
            (relation.canonical_payload() for relation in self.relations),
            key=canonical_json,
        )
        return {
            "schema_version": self.schema_version,
            "bundle_id": self.bundle_id,
            "entries": entries,
            "release_manifest": (
                self.release_manifest.canonical_payload()
                if self.release_manifest is not None
                else None
            ),
            "release_manifest_digest": (
                self.release_manifest.release_digest
                if self.release_manifest is not None
                else None
            ),
            "relations": relations,
            "metadata": self.metadata,
        }

    @property
    def bundle_digest(self) -> str:
        return content_hash(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class BundleArtifactReceipt:
    entry_key: str
    logical_id: str
    revision_id: str
    artifact: ArtifactReceipt

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "entry_key": self.entry_key,
            "logical_id": self.logical_id,
            "revision_id": self.revision_id,
            "artifact": {
                "relative_path": self.artifact.relative_path,
                "digest": self.artifact.digest,
                "size_bytes": self.artifact.size_bytes,
            },
        }


@dataclass(frozen=True, slots=True)
class BundleReceipt:
    bundle_id: str
    bundle_digest: str
    release_manifest_digest: str
    artifacts: tuple[BundleArtifactReceipt, ...]
    node_count: int
    edge_count: int

    def __post_init__(self) -> None:
        if not IDENTIFIER_RE.fullmatch(self.bundle_id):
            raise ValueError("receipt bundle_id must be normalized")
        if not DIGEST_RE.fullmatch(self.bundle_digest):
            raise ValueError("receipt bundle_digest must be a lowercase SHA-256 digest")
        _require_digest(self.release_manifest_digest, "receipt release manifest digest")
        if self.node_count != len(self.artifacts) or self.node_count <= 0:
            raise ValueError("receipt node count must match its artifacts")
        if self.edge_count < 0:
            raise ValueError("receipt edge count must be nonnegative")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_digest": self.bundle_digest,
            "release_manifest_digest": self.release_manifest_digest,
            "artifacts": [artifact.canonical_payload() for artifact in self.artifacts],
            "node_count": self.node_count,
            "edge_count": self.edge_count,
        }

    @property
    def receipt_digest(self) -> str:
        return content_hash(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class StoredMathBundle:
    bundle: MathKnowledgeBundle
    receipt: BundleReceipt
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


def _resolved_relation_ids(
    bundle: MathKnowledgeBundle,
) -> tuple[tuple[MathRelationSpec, str, str], ...]:
    revision_by_key = {entry.key: entry.revision.revision_id for entry in bundle.entries}

    def resolve(endpoint: BundleEndpoint) -> str:
        if endpoint.scope is EndpointScope.BUNDLE_ENTRY:
            return revision_by_key[endpoint.value]
        return endpoint.value

    return tuple(
        (relation, resolve(relation.source), resolve(relation.target))
        for relation in bundle.relations
    )


def _ensure_edge_endpoint_safety(
    relations: tuple[tuple[MathRelationSpec, str, str], ...],
    nodes: dict[str, GraphNode],
) -> None:
    for relation, source_id, target_id in relations:
        source = nodes[source_id]
        target = nodes[target_id]
        external_involved = (
            source.kind is NodeKind.EXTERNAL_REFERENCE
            or target.kind is NodeKind.EXTERNAL_REFERENCE
        )
        if external_involved:
            if not (
                relation.kind is EdgeKind.BRIDGES_TO
                and source.kind is NodeKind.CLAIM
                and target.kind is NodeKind.EXTERNAL_REFERENCE
            ):
                raise ValueError(
                    "external-reference proxies may only be targets of bridges_to edges"
                )
            continue
        if relation.kind is EdgeKind.BRIDGES_TO:
            raise ValueError("bridges_to must target an existing external-reference proxy")
        if relation.kind in CLAIM_TO_CLAIM_RELATIONS and (
            source.kind is not NodeKind.CLAIM or target.kind is not NodeKind.CLAIM
        ):
            raise ValueError(f"{relation.kind.value} bundle relations require claim endpoints")
        if relation.kind is EdgeKind.CITES and (
            source.kind is not NodeKind.CLAIM
            or target.kind not in {NodeKind.CLAIM, NodeKind.SOURCE}
        ):
            raise ValueError("cites bundle relations require claim-to-claim/source endpoints")


def _ensure_new_edges_are_acyclic(
    store: ResearchStore,
    edge_kind: EdgeKind,
    new_pairs: tuple[tuple[str, str], ...],
) -> None:
    if not new_pairs:
        return
    adjacency: dict[str, set[str]] = {}
    for edge in store.iter_edges(kind=edge_kind):
        adjacency.setdefault(edge.source_id, set()).add(edge.target_id)
    for source_id, target_id in new_pairs:
        adjacency.setdefault(source_id, set()).add(target_id)

    for source_id, target_id in new_pairs:
        frontier = [target_id]
        visited: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current == source_id:
                raise ValueError(f"new {edge_kind.value} edges would create a directed cycle")
            if current in visited:
                continue
            visited.add(current)
            frontier.extend(adjacency.get(current, ()))


def _edge_id(bundle_digest: str, payload: dict[str, Any]) -> str:
    identity = canonical_json({"bundle_digest": bundle_digest, "edge": payload})
    return "edge_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _materialize_edges(
    bundle: MathKnowledgeBundle,
    relations: tuple[tuple[MathRelationSpec, str, str], ...],
) -> tuple[GraphEdge, ...]:
    release = bundle.release_manifest
    if release is None:
        raise ValueError("cannot materialize edges while the release gate is dark")
    edge_specs: list[dict[str, Any]] = []
    for entry in bundle.entries:
        for previous_id in entry.revision.supersedes:
            edge_specs.append(
                {
                    "source_id": entry.revision.revision_id,
                    "target_id": previous_id,
                    "kind": EdgeKind.SUPERSEDES,
                    "rationale": (
                        "new immutable revision supersedes the cited prior revision"
                    ),
                    "weight": 1.0,
                    "metadata": {"logical_id": entry.revision.logical_id},
                }
            )
    for relation, source_id, target_id in relations:
        edge_specs.append(
            {
                "source_id": source_id,
                "target_id": target_id,
                "kind": relation.kind,
                "rationale": relation.rationale,
                "weight": relation.weight,
                "metadata": relation.metadata,
            }
        )

    edge_specs.sort(
        key=lambda item: canonical_json(
            {
                **item,
                "kind": item["kind"].value,
            }
        )
    )
    seen: set[tuple[str, str, EdgeKind]] = set()
    result: list[GraphEdge] = []
    for item in edge_specs:
        key = (item["source_id"], item["target_id"], item["kind"])
        if key in seen:
            raise ValueError("bundle relations collide with a generated typed edge")
        seen.add(key)
        identity_payload = {
            "source_id": item["source_id"],
            "target_id": item["target_id"],
            "kind": item["kind"].value,
            "rationale": item["rationale"],
            "weight": item["weight"],
            "metadata": item["metadata"],
        }
        result.append(
            GraphEdge(
                source_id=item["source_id"],
                target_id=item["target_id"],
                kind=item["kind"],
                rationale=item["rationale"],
                weight=item["weight"],
                metadata={
                    **item["metadata"],
                    "bundle_id": bundle.bundle_id,
                    "bundle_digest": bundle.bundle_digest,
                    "release_manifest_digest": release.release_digest,
                },
                id=_edge_id(bundle.bundle_digest, identity_payload),
                created_at=bundle.created_at,
            )
        )
    return tuple(result)


def ingest_bundle(
    store: ResearchStore,
    objects: ContentAddressedTextStore,
    bundle: MathKnowledgeBundle,
    *,
    release_verifier: ReleaseVerifier,
) -> StoredMathBundle:
    """Validate and append a complete mathematical bundle.

    Content-addressed Markdown is persisted before the SQLite transaction. If the
    database append fails, any resulting files are harmless immutable orphans and
    may be reused by a later valid append; no graph or claim row is partially added.
    """

    if not isinstance(bundle, MathKnowledgeBundle):
        raise TypeError("bundle must be a MathKnowledgeBundle")
    release = bundle.release_manifest
    if release is None or release.disposition is not ReleaseDisposition.APPROVED:
        raise ValueError("bundle release gate is dark; approved cross-bound manifest required")
    if not isinstance(release_verifier, ReleaseVerifier):
        raise TypeError("release_verifier must be configured by the trusted runtime")
    if not release_verifier.verify(release):
        raise ValueError("bundle release authorization receipt is invalid")

    entries_by_revision = {
        entry.revision.revision_id: entry for entry in bundle.entries
    }
    new_revision_ids = set(entries_by_revision)
    relations = _resolved_relation_ids(bundle)
    required_existing_ids = {
        endpoint.value
        for relation in bundle.relations
        for endpoint in (relation.source, relation.target)
        if endpoint.scope is EndpointScope.EXISTING_NODE
    }
    required_existing_ids.update(
        previous_id
        for entry in bundle.entries
        for previous_id in entry.revision.supersedes
        if previous_id not in new_revision_ids
    )

    existing = store.get_nodes(required_existing_ids | new_revision_ids)
    if store.get_bundle_manifest(bundle.bundle_id) is not None:
        raise ValueError("bundle id already exists; immutable bundles cannot be replayed")
    replayed_ids = sorted(new_revision_ids & set(existing))
    if replayed_ids:
        preview = ", ".join(replayed_ids[:3])
        raise ValueError(
            "bundle revisions already exist; immutable bundles cannot be replayed: "
            f"{preview}"
        )
    missing_ids = sorted(required_existing_ids - set(existing))
    if missing_ids:
        preview = ", ".join(missing_ids[:5])
        raise ValueError(f"bundle references missing existing nodes: {preview}")
    if store.schema_digest() != release.store_seal.schema_digest:
        raise ValueError("bundle store schema build seal is stale")
    if store.state_digest() != release.store_seal.state_digest:
        raise ValueError("bundle database pre-state seal is stale")

    for entry in bundle.entries:
        for previous_id in entry.revision.supersedes:
            if previous_id in entries_by_revision:
                target_kind = NodeKind.CLAIM
                target_logical_id = entries_by_revision[previous_id].revision.logical_id
            else:
                target = existing[previous_id]
                target_kind = target.kind
                target_logical_id = target.metadata.get("logical_id")
                target_release_digest = target.metadata.get("release_manifest_digest")
            if target_kind is not NodeKind.CLAIM or target_logical_id != entry.revision.logical_id:
                raise ValueError(
                    "supersedes must target a prior claim revision with the same logical_id"
                )
            if previous_id in entries_by_revision:
                raise ValueError("a bundle cannot supersede another revision in the same append")
            if (
                not isinstance(target_release_digest, str)
                or target_release_digest not in release.retires_release_digests
            ):
                raise ValueError(
                    "supersedes target is not named by an explicit retired release pointer"
                )

    node_types = dict(existing)
    for entry in bundle.entries:
        node_types[entry.revision.revision_id] = GraphNode(
            id=entry.revision.revision_id,
            kind=NodeKind.CLAIM,
            title=entry.revision.title,
            metadata={"logical_id": entry.revision.logical_id},
            created_at=entry.revision.created_at,
        )
    _ensure_edge_endpoint_safety(relations, node_types)
    edges = _materialize_edges(bundle, relations)

    pending_pairs: dict[EdgeKind, list[tuple[str, str]]] = {
        kind: [] for kind in ACYCLIC_EDGE_KINDS
    }
    for relation, source_id, target_id in relations:
        if relation.kind in pending_pairs:
            pending_pairs[relation.kind].append((source_id, target_id))
    for entry in bundle.entries:
        pending_pairs[EdgeKind.SUPERSEDES].extend(
            (entry.revision.revision_id, previous_id)
            for previous_id in entry.revision.supersedes
        )
    for kind, pairs in pending_pairs.items():
        _ensure_new_edges_are_acyclic(store, kind, tuple(pairs))

    stored_artifacts: list[BundleArtifactReceipt] = []
    nodes: list[GraphNode] = []
    batch_items: list[ClaimBatchItem] = []
    for entry in sorted(bundle.entries, key=lambda item: item.key):
        artifact = objects.persist(entry.revision)
        base_node = entry.revision.to_graph_node(artifact)
        node = GraphNode(
            id=base_node.id,
            kind=base_node.kind,
            title=base_node.title,
            body=base_node.body,
            metadata={
                **base_node.metadata,
                "bundle_id": bundle.bundle_id,
                "bundle_digest": bundle.bundle_digest,
                "bundle_entry_key": entry.key,
                "release_manifest_digest": release.release_digest,
            },
            schema_version=base_node.schema_version,
            created_at=base_node.created_at,
        )
        stored_artifacts.append(
            BundleArtifactReceipt(
                entry_key=entry.key,
                logical_id=entry.revision.logical_id,
                revision_id=entry.revision.revision_id,
                artifact=artifact,
            )
        )
        nodes.append(node)
        batch_items.append(ClaimBatchItem(entry.revision.to_claim(), node))

    manifest = ClaimBatchManifest(
        bundle_id=bundle.bundle_id,
        bundle_digest=bundle.bundle_digest,
        release_digest=release.release_digest,
        payload=bundle.canonical_payload(),
        members=tuple(
            (entry.key, entry.revision.revision_id)
            for entry in sorted(bundle.entries, key=lambda item: item.key)
        ),
        created_at=bundle.created_at,
    )
    store.add_claim_batch(
        batch_items,
        edges=edges,
        manifest=manifest,
        expected_schema_digest=release.store_seal.schema_digest,
        expected_state_digest=release.store_seal.state_digest,
    )
    receipt = BundleReceipt(
        bundle_id=bundle.bundle_id,
        bundle_digest=bundle.bundle_digest,
        release_manifest_digest=release.release_digest,
        artifacts=tuple(stored_artifacts),
        node_count=len(nodes),
        edge_count=len(edges),
    )
    return StoredMathBundle(bundle, receipt, tuple(nodes), edges)
