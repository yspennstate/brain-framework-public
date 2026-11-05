"""Immutable, content-addressed mathematical revisions for the overlay brain.

The overlay is independent storage that can be queried on its own.  Connections
to another brain are represented by deterministic external-reference proxy nodes;
writing the reciprocal edge in that other system remains a separate, claimed act.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from .models import (
    Claim,
    ClaimKind,
    EdgeKind,
    GraphEdge,
    LEGACY_READ_ONLY_STATUSES,
    GraphNode,
    NodeKind,
    RecordStatus,
    canonical_json,
    content_hash,
    utc_now,
)

if TYPE_CHECKING:
    from .store import ResearchStore


OVERLAY_SCHEMA_VERSION = 1
IDENTIFIER_RE = re.compile(r"[a-z0-9][a-z0-9._/-]{0,255}\Z")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _is_junction(path: Path) -> bool:
    """A Windows directory junction, which is_symlink() does not report."""
    check = getattr(os.path, "isjunction", None)
    return bool(check and check(path))


class TrustClass(StrEnum):
    """Provenance strength, deliberately separate from mathematical truth."""

    GENERATED_UNVERIFIED = "generated_unverified"
    CURATED_SECONDARY = "curated_secondary"
    PRIMARY_SOURCE = "primary_source"
    CERTIFICATE_CHECKED = "certificate_checked"
    KERNEL_CHECKED = "kernel_checked"


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    source_id: str
    source_revision: str
    locator: str
    imported_by: str
    trust_class: TrustClass = TrustClass.GENERATED_UNVERIFIED
    source_content_digest: str | None = None
    statement_fidelity_reviewed: bool = False

    def __post_init__(self) -> None:
        for name, value in (
            ("source_id", self.source_id),
            ("source_revision", self.source_revision),
            ("locator", self.locator),
            ("imported_by", self.imported_by),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        if self.source_content_digest is not None and not DIGEST_RE.fullmatch(
            self.source_content_digest
        ):
            raise ValueError("source_content_digest must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class ArtifactReceipt:
    relative_path: str
    digest: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.relative_path or Path(self.relative_path).is_absolute():
            raise ValueError("artifact path must be nonempty and relative")
        if not DIGEST_RE.fullmatch(self.digest):
            raise ValueError("artifact digest must be a lowercase SHA-256 digest")
        if self.size_bytes < 0:
            raise ValueError("artifact size must be nonnegative")


@dataclass(frozen=True, slots=True)
class MathNodeRevision:
    """One immutable state of one logical mathematical object."""

    namespace: str
    logical_name: str
    kind: ClaimKind
    title: str
    statement: str
    provenance: SourceProvenance
    assumptions: tuple[str, ...] = ()
    domain: str = ""
    # the revision's declared disposition (identity-bearing, never authority; see to_claim)
    status: RecordStatus = RecordStatus.UNVERIFIED
    conditions: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    disposition_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = OVERLAY_SCHEMA_VERSION
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        for name, value in (
            ("namespace", self.namespace),
            ("logical_name", self.logical_name),
        ):
            if not IDENTIFIER_RE.fullmatch(value):
                raise ValueError(f"{name} must be a normalized lowercase identifier")
        if not self.title.strip() or not self.statement.strip():
            raise ValueError("title and statement are required")
        if self.schema_version != OVERLAY_SCHEMA_VERSION:
            raise ValueError(f"unsupported overlay schema version: {self.schema_version}")
        for name, values in (
            ("assumptions", self.assumptions),
            ("conditions", self.conditions),
            ("evidence_ids", self.evidence_ids),
            ("supersedes", self.supersedes),
        ):
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise ValueError(f"{name} must contain nonempty strings")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
        if self.status in LEGACY_READ_ONLY_STATUSES:
            raise ValueError(f"{self.status.value} is a legacy stored status; a new revision cannot declare it")
        if self.status is RecordStatus.CONDITIONAL and not self.conditions:
            raise ValueError("conditional status requires explicit conditions")
        if self.status in {
            RecordStatus.PROVISIONAL,
            RecordStatus.CONDITIONAL,
            RecordStatus.VERIFIED,
        } and not self.evidence_ids:
            raise ValueError(f"{self.status.value} status requires explicit evidence ids")
        if (
            self.status is RecordStatus.VERIFIED
            and not self.provenance.statement_fidelity_reviewed
        ):
            raise ValueError("verified status requires statement-fidelity review")
        if self.status in {
            RecordStatus.GAP_FOUND,
            RecordStatus.REFUTED,
            RecordStatus.RETRACTED,
        } and not self.disposition_reason.strip():
            raise ValueError(f"{self.status.value} status requires a disposition reason")
        if self.revision_id in self.supersedes:
            raise ValueError("a revision cannot supersede itself")
        # Validate serializability now, before an append attempts filesystem I/O.
        canonical_json(self.canonical_payload())

    @property
    def logical_id(self) -> str:
        return f"math:{self.namespace}:{self.logical_name}"

    def canonical_payload(self) -> dict[str, Any]:
        """Return identity-bearing content, excluding ingestion time."""

        return {
            "schema_version": self.schema_version,
            "logical_id": self.logical_id,
            "kind": self.kind.value,
            "title": self.title,
            "statement": self.statement,
            "assumptions": self.assumptions,
            "domain": self.domain,
            "status": self.status.value,
            "conditions": self.conditions,
            "evidence_ids": self.evidence_ids,
            "supersedes": self.supersedes,
            "disposition_reason": self.disposition_reason,
            "provenance": asdict(self.provenance),
            "metadata": self.metadata,
        }

    @property
    def revision_digest(self) -> str:
        return content_hash(self.canonical_payload())

    @property
    def revision_id(self) -> str:
        return "claim_" + self.revision_digest.removeprefix("sha256:")

    def render_markdown(self) -> str:
        """Render deterministic human-readable text for content-addressed storage."""

        identity = canonical_json(
            {
                "logical_id": self.logical_id,
                "revision_id": self.revision_id,
                "revision_digest": self.revision_digest,
                **self.canonical_payload(),
            }
        )
        sections = [
            f"# {self.title}",
            "",
            "```json",
            identity,
            "```",
            "",
            "## Statement",
            "",
            self.statement,
        ]
        if self.assumptions:
            sections.extend(
                ["", "## Assumptions", "", *(f"- {item}" for item in self.assumptions)]
            )
        if self.conditions:
            sections.extend(
                ["", "## Conditions", "", *(f"- {item}" for item in self.conditions)]
            )
        if self.disposition_reason:
            sections.extend(["", "## Disposition", "", self.disposition_reason])
        return "\n".join(sections) + "\n"

    def to_claim(self) -> Claim:
        """The stored claim, which always starts unverified.

        The store admits no other initial status, and only authenticated verification events move a claim. The
        revision's own status is its declared disposition: part of its identity and recorded on its node as
        declared_status, never the claim's authority. Until 30 Sep the declared status was passed through here, so a
        revision declaring any of the seven other statuses could not be appended at all (account02/B11,
        B11-R79-OVERLAY-STATUS-CANNOT-APPEND-01)."""
        return Claim(
            statement=self.statement,
            kind=self.kind,
            assumptions=self.assumptions,
            domain=self.domain,
            status=RecordStatus.UNVERIFIED,
            id=self.revision_id,
            created_at=self.created_at,
        )

    def to_graph_node(self, artifact: ArtifactReceipt) -> GraphNode:
        body_parts = [self.statement, *self.assumptions, *self.conditions]
        # truth_status is the claim's status at append (always unverified, see to_claim; the full-note reader refuses a
        # node whose truth_status differs from its claim). A revision that declares another status carries it as
        # declared_status; an unverified revision's node is written exactly as before.
        declared = {} if self.status is RecordStatus.UNVERIFIED else {"declared_status": self.status.value}
        return GraphNode(
            id=self.revision_id,
            kind=NodeKind.CLAIM,
            title=self.title,
            body="\n".join(body_parts),
            metadata={
                **self.metadata,
                **declared,
                "overlay_schema_version": self.schema_version,
                "logical_id": self.logical_id,
                "revision_digest": self.revision_digest,
                "claim_kind": self.kind.value,
                "domain": self.domain,
                "truth_status": RecordStatus.UNVERIFIED.value,
                "conditions": self.conditions,
                "evidence_ids": self.evidence_ids,
                "supersedes": self.supersedes,
                "source_id": self.provenance.source_id,
                "source_revision": self.provenance.source_revision,
                "trust_class": self.provenance.trust_class.value,
                "statement_fidelity_reviewed": (
                    self.provenance.statement_fidelity_reviewed
                ),
                "artifact_path": artifact.relative_path,
                "artifact_digest": artifact.digest,
            },
            created_at=self.created_at,
        )

    def supersession_edges(self) -> tuple[GraphEdge, ...]:
        return tuple(
            GraphEdge(
                source_id=self.revision_id,
                target_id=previous_id,
                kind=EdgeKind.SUPERSEDES,
                rationale="new immutable revision supersedes the cited prior revision",
                metadata={"logical_id": self.logical_id},
            )
            for previous_id in self.supersedes
        )


class ContentAddressedTextStore:
    """Append-only SHA-256 object store for one text file per node revision."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _contained_parent(self, relative: Path) -> Path:
        """The target's directory, created inside the store and checked to stay there.

        mkdir(parents=True) follows a symbolic link or junction already standing on the way, so a
        store whose objects/ was a link to another directory wrote its objects there under names
        that looked right (account04/B19, B19-R69-OVERLAY-CONTAINMENT-PREMISE-20260929). Every
        existing component is checked before anything is created, and the directory is resolved
        and checked again after it is created."""
        current = self.root
        for part in relative.parent.parts:
            current = current / part
            if current.is_symlink() or _is_junction(current):
                raise OSError(
                    f"store path component {part!r} is a link; refusing to write through it"
                )
            if not current.exists():
                break
        parent = self.root / relative.parent
        parent.mkdir(parents=True, exist_ok=True)
        real = parent.resolve()
        if real != self.root and self.root not in real.parents:
            raise OSError("content-addressed target resolves outside the store")
        return parent

    def persist(self, revision: MathNodeRevision) -> ArtifactReceipt:
        raw = revision.render_markdown().encode("utf-8")
        digest_hex = hashlib.sha256(raw).hexdigest()
        relative = Path("objects") / "sha256" / digest_hex[:2] / f"{digest_hex}.md"
        target = self._contained_parent(relative) / relative.name
        if target.is_symlink():
            raise OSError("content-addressed target must not be a symbolic link")
        if target.exists():
            if not target.is_file() or target.read_bytes() != raw:
                raise OSError("existing content-addressed object does not match its digest")
            return ArtifactReceipt(relative.as_posix(), f"sha256:{digest_hex}", len(raw))

        temporary = target.with_name(f".{digest_hex}.{uuid4().hex}.tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            view = memoryview(raw)
            written = 0
            while written < len(view):
                count = os.write(descriptor, view[written:])
                if count <= 0:
                    raise OSError("content-addressed object write made no progress")
                written += count
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            try:
                os.link(temporary, target)
            except FileExistsError:
                if not target.is_file() or target.read_bytes() != raw:
                    raise OSError("content-addressed object collision") from None
        finally:
            temporary.unlink(missing_ok=True)
        return ArtifactReceipt(relative.as_posix(), f"sha256:{digest_hex}", len(raw))


@dataclass(frozen=True, slots=True)
class StoredMathRevision:
    revision: MathNodeRevision
    artifact: ArtifactReceipt
    node: GraphNode


def append_revision(
    store: ResearchStore,
    objects: ContentAddressedTextStore,
    revision: MathNodeRevision,
) -> StoredMathRevision:
    """Persist text first, then atomically append the claim, node, and revision edges."""

    artifact = objects.persist(revision)
    node = revision.to_graph_node(artifact)
    store.add_claim(
        revision.to_claim(),
        node=node,
        edges=revision.supersession_edges(),
    )
    return StoredMathRevision(revision, artifact, node)


def external_reference_node(
    external_namespace: str,
    external_id: str,
    title: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> GraphNode:
    """Create a deterministic local proxy for a node owned by another brain."""

    if not IDENTIFIER_RE.fullmatch(external_namespace):
        raise ValueError("external namespace must be a normalized lowercase identifier")
    if not external_id.strip() or not title.strip():
        raise ValueError("external id and title are required")
    payload = {
        "external_namespace": external_namespace,
        "external_id": external_id,
        "title": title,
        "metadata": metadata or {},
    }
    digest = content_hash(payload).removeprefix("sha256:")
    return GraphNode(
        id=f"external_{digest}",
        kind=NodeKind.EXTERNAL_REFERENCE,
        title=title,
        metadata=payload,
    )


def bridge_edge(source_id: str, external_node_id: str, rationale: str) -> GraphEdge:
    """Connect an overlay node to an external proxy without merging the stores."""

    return GraphEdge(
        source_id=source_id,
        target_id=external_node_id,
        kind=EdgeKind.BRIDGES_TO,
        rationale=rationale,
    )
