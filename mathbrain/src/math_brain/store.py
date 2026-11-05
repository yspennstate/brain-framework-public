"""Append-only SQLite store for mathematical claims, evidence, and graph edges."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import warnings
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from .mistakes import MistakeKind, MistakeRecord, ReflexRecord
from .models import (
    Claim,
    ClaimKind,
    EdgeKind,
    Evidence,
    EvidenceAttestation,
    EvidenceAuthorityMode,
    EvidenceAuthorityRole,
    EvidenceKind,
    EvidenceReceipt,
    GraphEdge,
    GraphNode,
    NodeKind,
    RecordStatus,
    VerificationEvent,
    canonical_json,
    content_hash,
    utc_now,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS bundle_manifests (
    id TEXT PRIMARY KEY,
    digest TEXT NOT NULL UNIQUE,
    release_digest TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY REFERENCES nodes(id),
    payload_json TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY REFERENCES nodes(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    payload_json TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_attestations (
    evidence_id TEXT PRIMARY KEY REFERENCES evidence(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    nonce TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_receipts (
    evidence_id TEXT PRIMARY KEY REFERENCES evidence(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    nonce TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mistakes (
    id TEXT PRIMARY KEY REFERENCES nodes(id),
    claim_id TEXT NOT NULL REFERENCES claims(id),
    payload_json TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reflexes (
    id TEXT PRIMARY KEY REFERENCES nodes(id),
    payload_json TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reflex_sources (
    reflex_id TEXT NOT NULL REFERENCES reflexes(id),
    mistake_id TEXT NOT NULL REFERENCES mistakes(id),
    PRIMARY KEY(reflex_id, mistake_id)
);
CREATE TABLE IF NOT EXISTS verification_events (
    id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(id),
    payload_json TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verification_event_evidence (
    event_id TEXT NOT NULL REFERENCES verification_events(id),
    evidence_id TEXT NOT NULL REFERENCES evidence_receipts(evidence_id),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    PRIMARY KEY(event_id, evidence_id),
    UNIQUE(event_id, ordinal)
);
CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES nodes(id),
    target_id TEXT NOT NULL REFERENCES nodes(id),
    kind TEXT NOT NULL,
    rationale TEXT NOT NULL,
    weight REAL NOT NULL CHECK(weight > 0 AND weight <= 1),
    metadata_json TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    UNIQUE(source_id, target_id, kind)
);
CREATE TABLE IF NOT EXISTS bundle_members (
    bundle_id TEXT NOT NULL REFERENCES bundle_manifests(id),
    entry_key TEXT NOT NULL,
    node_id TEXT NOT NULL REFERENCES nodes(id),
    PRIMARY KEY(bundle_id, entry_key),
    UNIQUE(bundle_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_evidence_claim ON evidence(claim_id);
CREATE INDEX IF NOT EXISTS idx_evidence_attestations_claim
    ON evidence_attestations(claim_id);
CREATE INDEX IF NOT EXISTS idx_evidence_receipts_claim ON evidence_receipts(claim_id);
CREATE INDEX IF NOT EXISTS idx_mistakes_claim ON mistakes(claim_id);
CREATE INDEX IF NOT EXISTS idx_reflex_sources_mistake ON reflex_sources(mistake_id);
CREATE INDEX IF NOT EXISTS idx_events_claim_time ON verification_events(claim_id, created_at);
CREATE INDEX IF NOT EXISTS idx_event_evidence_id ON verification_event_evidence(evidence_id);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_bundle_members_node ON bundle_members(node_id);
"""

# An edge found wrong is retracted, not deleted: a row here names it and says why, and iter_edges and
# neighbors skip it. The first retraction creates the table, so SCHEMA_SQL, schema_digest and every store
# that never retracts are unchanged and STORE_SCHEMA_VERSION stays 1. A retraction is final: the edge row
# keeps its (source, target, kind), so the same edge cannot be added again.
EDGE_RETRACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS edge_retractions (
    edge_id TEXT PRIMARY KEY REFERENCES edges(id),
    reason TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    digest TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
)
"""


#: longest title a default claim node receives; cut at a word, never mid-token
CLAIM_NODE_TITLE_CHARS = 140


def _default_claim_node(claim: Claim) -> GraphNode:
    """Project a claim into its graph node: short title, full statement in the body.

    The first projection put the whole statement in the title and ONLY the
    assumptions in the body. Every downstream renderer reads title as a label and
    body as the content, so a conditional claim was served with its assumptions
    printed as if they were the statement, and its real statement rode in a
    thousand-character "title" that blew any block budget it met. The store's
    1,155 pre-existing nodes still have the old shape; this fixes appends only.
    """

    title = " ".join(claim.statement.split())
    if len(title) > CLAIM_NODE_TITLE_CHARS:
        cut = title[:CLAIM_NODE_TITLE_CHARS]
        title = cut[: cut.rfind(" ")] if " " in cut else cut
    body = claim.statement
    if claim.assumptions:
        body += "\n\nAssumes: " + "; ".join(claim.assumptions)
    return GraphNode(
        id=claim.id,
        kind=NodeKind.CLAIM,
        title=title,
        body=body,
        metadata={"claim_kind": claim.kind.value, "domain": claim.domain},
        created_at=claim.created_at,
    )


@dataclass(frozen=True, slots=True)
class ClaimBatchItem:
    """One claim and its optional richer graph representation in a batch append."""

    claim: Claim
    node: GraphNode | None = None


@dataclass(frozen=True, slots=True)
class ClaimBatchManifest:
    """Exact bundle manifest persisted beside an atomic claim batch."""

    bundle_id: str
    bundle_digest: str
    release_digest: str
    payload: dict[str, object]
    members: tuple[tuple[str, str], ...]
    created_at: str


STORE_SCHEMA_VERSION = 1
"""The newest store layout this reader understands; a store stamped higher refuses to open."""


class StoreSchemaError(RuntimeError):
    """The existing store schema cannot be read by this code."""


_SCHEMA_TABLES: frozenset[str] | None = None


def _schema_tables() -> frozenset[str]:
    """The table names SCHEMA_SQL creates, read once from an in-memory database."""
    global _SCHEMA_TABLES
    if _SCHEMA_TABLES is None:
        memory = sqlite3.connect(":memory:")
        try:
            memory.executescript(SCHEMA_SQL)
            _SCHEMA_TABLES = frozenset(
                row[0]
                for row in memory.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        finally:
            memory.close()
    return _SCHEMA_TABLES


class UnknownEdgeKindError(ValueError):
    """An edge row carries a kind this build's EdgeKind does not define.

    Task 462 (2026-08-09): a reader built from an older EdgeKind met ``semantic_association``
    and ``supersedes`` rows in the population store and ``iter_edges`` died mid-iteration with a
    bare ``ValueError``; ``recall`` crashed. The failure is now named (kind, row count, store) and
    the caller chooses: upgrade the reader, or pass ``skip_unknown=True`` and accept a warning.
    """

    def __init__(self, kind: str, count: int, store: Path) -> None:
        self.kind = kind
        self.count = count
        self.store = store
        super().__init__(
            f"edge kind {kind!r} ({count} row{'s' if count != 1 else ''} in {store}) is not "
            f"defined by this build's EdgeKind; upgrade the reader, or pass skip_unknown=True "
            f"(CLI: --skip-unknown-edges) to skip those rows with a warning"
        )


class UnknownEdgeKindWarning(UserWarning):
    """Emitted once per unknown edge kind when a caller opted into skipping."""


class ResearchStore:
    """Small trusted persistence layer with no update/delete API."""

    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        if read_only:
            # Strict resolution and mode=ro prohibit accidental creation. Do not use
            # immutable=1: live WAL commits must remain visible to this reader.
            self.path = self.path.resolve(strict=True)
            self.connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            self.connection.row_factory = sqlite3.Row
            try:
                self.connection.execute("PRAGMA query_only = ON")
                self.connection.execute("PRAGMA foreign_keys = ON")
                row = self.connection.execute(
                    "SELECT MAX(version) AS v FROM schema_meta"
                ).fetchone()
                version = row["v"] if row is not None else None
                if type(version) is not int or version != STORE_SCHEMA_VERSION:
                    raise StoreSchemaError(
                        f"Read-only store requires schema version {STORE_SCHEMA_VERSION}; "
                        f"found {version!r}. Initialize or migrate with a writable owner first."
                    )
            except sqlite3.DatabaseError as exc:
                self.connection.close()
                raise StoreSchemaError("Cannot read the existing store schema") from exc
            except BaseException:
                self.connection.close()
                raise
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        try:
            self._preflight_existing()
        except BaseException:
            self.connection.close()
            raise
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(SCHEMA_SQL)
        self.connection.execute(
            "INSERT OR IGNORE INTO schema_meta(version) VALUES (?)", (STORE_SCHEMA_VERSION,)
        )
        self.connection.commit()
        self._gate_schema_version()

    @classmethod
    def open_read_only(cls, path: str | Path) -> ResearchStore:
        """Open an existing store without schema initialization or data writes.

        SQLite may create an empty WAL and coordinate through a shared-memory sidecar;
        this API does not promise an unchanged directory or no sidecar metadata writes.
        This is not an immutable snapshot: callers that need a consistent multi-query
        view should explicitly BEGIN a read transaction and close it promptly.
        """
        return cls(path, read_only=True)

    def _preflight_existing(self) -> None:
        """Refuse a database this build must not initialize, before anything is written to it.

        The writable open turns on WAL, runs the schema script and records the current version.
        It used to ask what version the file carried only after all that, so a newer store it
        refused had already been given WAL, this build's tables and a second version row
        (interactive audit IA-20260929-052554-02, 29 Sep 2026). This read changes nothing.

        A file with no tables is a new store. A file whose schema_meta is empty is accepted only
        when every table in it is one this build's schema creates: that is this build's own
        initialization cut off before the version row, and the open finishes it. Refused: tables
        with no schema_meta, an empty schema_meta beside tables this build does not create, a
        version that is not an integer, and a version newer than this build understands. Every
        Math Brain store has carried schema_meta since the store's first commit."""
        try:
            tables = {
                row[0]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
                if not str(row[0]).startswith("sqlite_")
            }
            if not tables:
                return
            if "schema_meta" not in tables:
                raise StoreSchemaError(
                    f"{self.path} holds tables but no schema_meta; "
                    "not a store this build may initialize"
                )
            row = self.connection.execute("SELECT MAX(version) AS v FROM schema_meta").fetchone()
        except sqlite3.DatabaseError as exc:
            raise StoreSchemaError(
                f"Cannot read the existing store schema of {self.path}"
            ) from exc
        version = row["v"] if row is not None else None
        if version is None:
            foreign = sorted(tables - _schema_tables())
            if not foreign:
                return
            raise StoreSchemaError(
                f"{self.path} records no schema version and holds tables this build does not "
                f"create ({', '.join(foreign[:5])}); not a store this build may initialize"
            )
        if type(version) is not int:
            raise StoreSchemaError(
                f"{self.path} carries an unreadable schema version ({version!r})"
            )
        if version > STORE_SCHEMA_VERSION:
            raise StoreSchemaError(
                f"{self.path} carries store schema version {version}; this reader understands "
                f"up to {STORE_SCHEMA_VERSION}. Upgrade the reader instead of tolerating the rows."
            )

    def _gate_schema_version(self) -> None:
        row = self.connection.execute("SELECT MAX(version) AS v FROM schema_meta").fetchone()
        stored = int(row["v"]) if row is not None and row["v"] is not None else 0
        if stored > STORE_SCHEMA_VERSION:
            self.connection.close()
            raise StoreSchemaError(
                f"{self.path} carries store schema version {stored}; this reader understands "
                f"up to {STORE_SCHEMA_VERSION}. Upgrade the reader instead of tolerating the rows."
            )

    def _edge_from_row(
        self, row: sqlite3.Row, *, skip_unknown: bool, warned: set[str]
    ) -> GraphEdge | None:
        try:
            kind = EdgeKind(row["kind"])
        except ValueError:
            raw = str(row["kind"])
            count = int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM edges WHERE kind = ?", (raw,)
                ).fetchone()[0]
            )
            if not skip_unknown:
                raise UnknownEdgeKindError(raw, count, self.path) from None
            if raw not in warned:
                warned.add(raw)
                warnings.warn(
                    f"skipping {count} edge row{'s' if count != 1 else ''} of unknown kind "
                    f"{raw!r} in {self.path} (skip_unknown=True)",
                    UnknownEdgeKindWarning,
                    stacklevel=3,
                )
            return None
        return GraphEdge(
            id=row["id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            kind=kind,
            rationale=row["rationale"],
            weight=row["weight"],
            metadata=json.loads(row["metadata_json"]),
            created_at=row["created_at"],
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ResearchStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def transaction(self) -> sqlite3.Connection:
        return self.connection

    @contextmanager
    def immediate_transaction(self) -> Iterator[sqlite3.Connection]:
        """Hold an immediate write lock and commit or roll back exactly once."""

        if self.connection.in_transaction:
            raise RuntimeError("immediate transaction requires a clean boundary")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def add_node(self, node: GraphNode) -> None:
        self.connection.execute(
            """INSERT INTO nodes(id, kind, title, body, metadata_json, digest, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                node.id,
                node.kind.value,
                node.title,
                node.body,
                canonical_json(node.metadata),
                node.digest(),
                node.created_at,
            ),
        )

    def add_claim(
        self,
        claim: Claim,
        *,
        node: GraphNode | None = None,
        edges: Iterable[GraphEdge] = (),
    ) -> None:
        """Append a claim and optional pre-built graph representation atomically.

        The default representation preserves the original API.  Overlay callers may
        supply a richer node carrying immutable revision and text-artifact metadata.
        Any accompanying edges are committed in the same SQLite transaction, so a
        missing superseded revision cannot leave a half-appended claim behind.
        """

        edge_list = tuple(edges)
        if any(edge.source_id != claim.id for edge in edge_list):
            raise ValueError("claim append edges must originate at the appended claim")
        self.add_claim_batch((ClaimBatchItem(claim, node),), edges=edge_list)

    def add_claim_batch(
        self,
        items: Iterable[ClaimBatchItem],
        *,
        edges: Iterable[GraphEdge] = (),
        manifest: ClaimBatchManifest | None = None,
        expected_schema_digest: str | None = None,
        expected_state_digest: str | None = None,
    ) -> None:
        """Append multiple claims and their edges in one all-or-nothing transaction.

        Validation is completed before the first insert. Edge endpoints may refer to
        another claim in this batch or to a node already present in the append-only
        store. A database constraint failure rolls back every node, claim, and edge in
        the batch.
        """

        item_list = tuple(items)
        if not item_list:
            raise ValueError("claim batch must not be empty")
        if any(not isinstance(item, ClaimBatchItem) for item in item_list):
            raise TypeError("claim batch items must be ClaimBatchItem records")
        if self.connection.in_transaction:
            raise RuntimeError("claim batch requires a clean transaction boundary")

        claim_ids = tuple(item.claim.id for item in item_list)
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("claim batch must not contain duplicate claim ids")
        if any(item.claim.status is not RecordStatus.UNVERIFIED for item in item_list):
            raise ValueError(
                "persisted claims must start unverified; use derived status events"
            )

        prepared: list[tuple[Claim, GraphNode, dict[str, object]]] = []
        for item in item_list:
            claim = item.claim
            claim_node = item.node or _default_claim_node(claim)
            if claim_node.id != claim.id or claim_node.kind is not NodeKind.CLAIM:
                raise ValueError("a custom claim node must be a claim with the same id")
            payload = asdict(claim)
            # Fail before the transaction if either payload is not canonical-JSON safe.
            canonical_json(payload)
            canonical_json(claim_node.metadata)
            prepared.append((claim, claim_node, payload))

        edge_list = tuple(edges)
        edge_ids = tuple(edge.id for edge in edge_list)
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("claim batch must not contain duplicate edge ids")
        edge_keys = tuple((edge.source_id, edge.target_id, edge.kind) for edge in edge_list)
        if len(set(edge_keys)) != len(edge_keys):
            raise ValueError("claim batch must not contain duplicate typed edges")
        for edge in edge_list:
            canonical_json(edge.metadata)

        if manifest is not None:
            if not isinstance(manifest, ClaimBatchManifest):
                raise TypeError("claim batch manifest must be a ClaimBatchManifest")
            if not manifest.bundle_id.strip() or not manifest.created_at.strip():
                raise ValueError("claim batch manifest id and timestamp are required")
            canonical_json(manifest.payload)
            if content_hash(manifest.payload) != manifest.bundle_digest:
                raise ValueError("claim batch manifest digest does not match its payload")
            if manifest.payload.get("bundle_id") != manifest.bundle_id:
                raise ValueError("claim batch manifest payload binds a different bundle id")
            if manifest.payload.get("release_manifest_digest") != manifest.release_digest:
                raise ValueError("claim batch manifest payload binds a different release")
            member_keys = tuple(entry_key for entry_key, _ in manifest.members)
            member_node_ids = tuple(node_id for _, node_id in manifest.members)
            if (
                not manifest.members
                or len(set(member_keys)) != len(member_keys)
                or len(set(member_node_ids)) != len(member_node_ids)
            ):
                raise ValueError("claim batch manifest members must be nonempty and unique")
            if set(member_node_ids) != set(claim_ids):
                raise ValueError("claim batch manifest must bind every appended claim exactly once")

        new_node_ids = set(claim_ids)
        referenced_ids = {
            endpoint
            for edge in edge_list
            for endpoint in (edge.source_id, edge.target_id)
            if endpoint not in new_node_ids
        }
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if (
                expected_schema_digest is not None
                and self.schema_digest() != expected_schema_digest
            ):
                raise ValueError("claim batch schema build seal is stale")
            if (
                expected_state_digest is not None
                and self.state_digest() != expected_state_digest
            ):
                raise ValueError("claim batch database pre-state seal is stale")

            if manifest is not None:
                self.connection.execute(
                    """INSERT INTO bundle_manifests(
                           id, digest, release_digest, payload_json, created_at
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        manifest.bundle_id,
                        manifest.bundle_digest,
                        manifest.release_digest,
                        canonical_json(manifest.payload),
                        manifest.created_at,
                    ),
                )

            existing_ids = set(self.get_nodes(referenced_ids))
            missing_ids = sorted(referenced_ids - existing_ids)
            if missing_ids:
                preview = ", ".join(missing_ids[:5])
                suffix = " ..." if len(missing_ids) > 5 else ""
                raise ValueError(
                    f"claim batch is not appendable; missing nodes: {preview}{suffix}"
                )

            for claim, claim_node, payload in prepared:
                self.add_node(claim_node)
                self.connection.execute(
                    """INSERT INTO claims(id, payload_json, digest, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        claim.id,
                        canonical_json(payload),
                        claim.digest(),
                        claim.created_at,
                    ),
                )
            if manifest is not None:
                for entry_key, node_id in manifest.members:
                    self.connection.execute(
                        """INSERT INTO bundle_members(bundle_id, entry_key, node_id)
                           VALUES (?, ?, ?)""",
                        (manifest.bundle_id, entry_key, node_id),
                    )
            for edge in edge_list:
                self.add_edge(edge, commit=False)
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError(f"claim batch is not appendable: {exc}") from exc
        except BaseException:
            self.connection.rollback()
            raise

    def add_evidence(self, evidence: Evidence, *, link: bool = True) -> None:
        """Append unauthenticated evidence for discovery, never as proof authority."""

        try:
            with self.connection:
                self._insert_evidence(evidence, link=link, authenticated=False)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"evidence is not appendable: {exc}") from exc

    def _insert_evidence(
        self,
        evidence: Evidence,
        *,
        link: bool,
        authenticated: bool,
    ) -> None:
        payload = asdict(evidence)
        self.add_node(
            GraphNode(
                id=evidence.id,
                kind=NodeKind.EVIDENCE,
                title=evidence.summary,
                body="\n".join(evidence.limitations),
                metadata={
                    "evidence_kind": evidence.kind.value,
                    "scope": evidence.scope,
                    "passed": evidence.passed,
                    "authenticated": authenticated,
                },
                created_at=evidence.created_at,
            )
        )
        self.connection.execute(
            """INSERT INTO evidence(id, claim_id, payload_json, digest, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                evidence.id,
                evidence.claim_id,
                canonical_json(payload),
                evidence.digest(),
                evidence.created_at,
            ),
        )
        if link:
            if not authenticated or not evidence.passed:
                edge_kind = EdgeKind.SEMANTIC_ASSOCIATION
            else:
                edge_kind = (
                    EdgeKind.REFUTES
                    if evidence.kind is EvidenceKind.COUNTEREXAMPLE and evidence.passed
                    else EdgeKind.VERIFIED_BY
                )
            self.add_edge(
                GraphEdge(
                    source_id=evidence.claim_id,
                    target_id=evidence.id,
                    kind=edge_kind,
                    rationale=f"Evidence record {evidence.id}",
                    metadata={
                        "passed": evidence.passed,
                        "authenticated": authenticated,
                    },
                ),
                commit=False,
            )

    def _add_authenticated_evidence(
        self,
        evidence: Evidence,
        attestation: EvidenceAttestation,
        receipt: EvidenceReceipt,
        *,
        link: bool = True,
    ) -> None:
        """Persist pre-authenticated evidence and its receipt in one transaction."""

        if (
            attestation.evidence_id != evidence.id
            or attestation.claim_id != evidence.claim_id
            or attestation.evidence_digest != evidence.digest()
            or attestation.evidence_kind is not evidence.kind
            or attestation.artifact_hash != evidence.artifact_hash
            or attestation.passed is not evidence.passed
            or receipt.evidence_id != evidence.id
            or receipt.claim_id != evidence.claim_id
            or receipt.evidence_digest != evidence.digest()
            or receipt.attestation_digest != attestation.digest()
            or receipt.evidence_kind is not evidence.kind
            or receipt.artifact_hash != evidence.artifact_hash
            or receipt.authority_key_id != attestation.authority_key_id
            or receipt.authority_role is not attestation.authority_role
            or receipt.nonce != attestation.nonce
            or receipt.verifier_digest != attestation.verifier_digest
            or receipt.toolchain_digest != attestation.toolchain_digest
            or receipt.source_digest != attestation.source_digest
        ):
            raise ValueError(
                "evidence attestation and receipt do not bind the exact evidence row"
            )
        try:
            with self.immediate_transaction():
                claim = self.get_claim(evidence.claim_id)
                if (
                    claim is None
                    or attestation.claim_digest != claim.digest()
                    or receipt.claim_digest != claim.digest()
                ):
                    raise ValueError(
                        "evidence authority records do not bind persisted claim content"
                    )
                self._insert_evidence(evidence, link=link, authenticated=True)
                self.connection.execute(
                    """INSERT INTO evidence_attestations(
                           evidence_id, claim_id, nonce, payload_json, digest, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        attestation.evidence_id,
                        attestation.claim_id,
                        attestation.nonce,
                        canonical_json(asdict(attestation)),
                        attestation.digest(),
                        attestation.created_at,
                    ),
                )
                self.connection.execute(
                    """INSERT INTO evidence_receipts(
                           evidence_id, claim_id, nonce, payload_json, digest, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        receipt.evidence_id,
                        receipt.claim_id,
                        receipt.nonce,
                        canonical_json(asdict(receipt)),
                        receipt.digest(),
                        receipt.created_at,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"authenticated evidence is not appendable: {exc}") from exc

    def add_mistake(self, mistake: MistakeRecord, *, link: bool = True) -> None:
        try:
            with self.connection:
                self.add_node(mistake.to_node())
                self.connection.execute(
                    """INSERT INTO mistakes(
                           id, claim_id, payload_json, digest, created_at
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        mistake.id,
                        mistake.claim_id,
                        canonical_json(asdict(mistake)),
                        mistake.digest(),
                        mistake.created_at,
                    ),
                )
                if link:
                    self.add_edge(
                        GraphEdge(
                            source_id=mistake.claim_id,
                            target_id=mistake.id,
                            kind=EdgeKind.FAILS_UNDER,
                            rationale=(
                                "A structured mistake records a condition under which the claim "
                                "or its attempted proof failed"
                            ),
                            metadata={"mistake_kind": mistake.kind.value},
                        ),
                        commit=False,
                    )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"mistake is not appendable: {exc}") from exc

    def add_reflex(self, reflex: ReflexRecord, *, link: bool = True) -> None:
        try:
            with self.connection:
                self.add_node(reflex.to_node())
                self.connection.execute(
                    """INSERT INTO reflexes(id, payload_json, digest, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        reflex.id,
                        canonical_json(asdict(reflex)),
                        reflex.digest(),
                        reflex.created_at,
                    ),
                )
                for mistake_id in reflex.source_mistake_ids:
                    self.connection.execute(
                        "INSERT INTO reflex_sources(reflex_id, mistake_id) VALUES (?, ?)",
                        (reflex.id, mistake_id),
                    )
                    if link:
                        self.add_edge(
                            GraphEdge(
                                source_id=mistake_id,
                                target_id=reflex.id,
                                kind=EdgeKind.TRIGGERS,
                                rationale=(
                                    "Replaying the cited mistake activates a concrete review reflex"
                                ),
                                metadata={"required_evidence": reflex.required_evidence},
                            ),
                            commit=False,
                        )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"reflex is not appendable: {exc}") from exc

    def add_verification_event(self, event: VerificationEvent) -> None:
        del event
        raise RuntimeError(
            "direct verification-event insertion is disabled; "
            "use the authenticated derived-status path"
        )

    def _add_derived_verification_event(self, event: VerificationEvent) -> None:
        if not self.connection.in_transaction:
            raise RuntimeError("derived verification event requires an active transaction")
        if not event.evidence_ids or len(set(event.evidence_ids)) != len(
            event.evidence_ids
        ):
            raise ValueError("derived verification event requires unique evidence receipts")
        for evidence_id in event.evidence_ids:
            row = self.connection.execute(
                """SELECT claim_id FROM evidence_receipts
                   WHERE evidence_id = ?""",
                (evidence_id,),
            ).fetchone()
            if row is None or row["claim_id"] != event.claim_id:
                raise ValueError(
                    "derived verification event requires authenticated evidence foreign keys"
                )
        self.connection.execute(
            """INSERT INTO verification_events(
                   id, claim_id, payload_json, digest, created_at
               ) VALUES (?, ?, ?, ?, ?)""",
            (
                event.id,
                event.claim_id,
                canonical_json(asdict(event)),
                event.digest(),
                event.created_at,
            ),
        )
        for ordinal, evidence_id in enumerate(event.evidence_ids):
            self.connection.execute(
                """INSERT INTO verification_event_evidence(
                       event_id, evidence_id, ordinal
                   ) VALUES (?, ?, ?)""",
                (event.id, evidence_id, ordinal),
            )

    def add_edge(self, edge: GraphEdge, *, commit: bool = True) -> None:
        self.connection.execute(
            """INSERT INTO edges(
                   id, source_id, target_id, kind, rationale, weight,
                   metadata_json, digest, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                edge.id,
                edge.source_id,
                edge.target_id,
                edge.kind.value,
                edge.rationale,
                edge.weight,
                canonical_json(edge.metadata),
                edge.digest(),
                edge.created_at,
            ),
        )
        if commit:
            self.connection.commit()

    def has_edge_retractions(self) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'edge_retractions'"
        ).fetchone()
        return row is not None

    def retracted_edge_ids(self) -> frozenset[str]:
        if not self.has_edge_retractions():
            return frozenset()
        return frozenset(
            row[0] for row in self.connection.execute("SELECT edge_id FROM edge_retractions")
        )

    def retract_edge(
        self,
        edge_id: str,
        reason: str,
        *,
        metadata: dict[str, object] | None = None,
        created_at: str | None = None,
        commit: bool = True,
    ) -> str:
        """Retract one edge and return the retraction's digest.

        The edge row stays; a row in edge_retractions names it, with the reason, and the edge
        readers skip it from then on. Refused: a read-only store, an unknown edge, a supersedes
        edge (a revision is retired by superseding it again, never by unhooking its history), an
        edge already retracted, and an empty reason.
        """

        if self.read_only:
            raise ValueError("cannot retract an edge in a read-only store")
        reason = (reason or "").strip()
        if not reason:
            raise ValueError("an edge retraction needs a reason")
        row = self.connection.execute(
            "SELECT kind, digest FROM edges WHERE id = ?", (edge_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"no edge {edge_id!r} in this store")
        if row["kind"] == EdgeKind.SUPERSEDES.value:
            raise ValueError("a supersedes edge cannot be retracted")
        if edge_id in self.retracted_edge_ids():
            raise ValueError(f"edge {edge_id!r} is already retracted")
        meta = dict(metadata or {})
        when = created_at or utc_now()
        digest = content_hash(
            {
                "edge_id": edge_id,
                "edge_digest": row["digest"],
                "reason": reason,
                "metadata": meta,
                "created_at": when,
            }
        )
        # DDL opens no transaction by itself here, so begin one: the table and its first row
        # commit or roll back together.
        began = not self.connection.in_transaction
        if began:
            self.connection.execute("BEGIN")
        try:
            self.connection.execute(EDGE_RETRACTIONS_SQL)
            self.connection.execute(
                """INSERT INTO edge_retractions(edge_id, reason, metadata_json, digest, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (edge_id, reason, canonical_json(meta), digest, when),
            )
        except BaseException:
            if began:
                self.connection.rollback()
            raise
        if commit:
            self.connection.commit()
        return digest

    def get_claim(self, claim_id: str) -> Claim | None:
        row = self.connection.execute(
            """SELECT id, digest, created_at, payload_json FROM claims
               WHERE id = ?""",
            (claim_id,),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["kind"] = ClaimKind(payload["kind"])
        payload["status"] = RecordStatus(payload["status"])
        payload["assumptions"] = tuple(payload["assumptions"])
        claim = Claim(**payload)
        if (
            claim.id != row["id"]
            or claim.created_at != row["created_at"]
            or claim.digest() != row["digest"]
        ):
            raise ValueError("stored claim columns do not match its payload")
        return claim

    def get_node(self, node_id: str) -> GraphNode | None:
        return self.get_nodes((node_id,)).get(node_id)

    def schema_digest(self) -> str:
        """Hash the exact append-only schema used to build this store."""

        return content_hash({"schema_version": 1, "schema_sql": SCHEMA_SQL})

    def state_digest(self) -> str:
        """Hash all append-only row identities for optimistic build sealing.

        It hashes each row's identity and recorded digest, not its payload bytes, so a payload
        changed under an unchanged digest column leaves this value unchanged. The content binding
        is enforced where a record is rebuilt: get_claim, the evidence, receipt, attestation and
        verification-event readers, get_bundle_manifest, get_mistake and get_reflex compare the
        rebuilt record with its row and refuse a mismatch, and the note reader checks nodes.
        Edge retractions are hashed last and only once one exists, so a store that never
        retracted an edge keeps the digest it had.
        """

        queries = (
            ("schema_meta", "SELECT version FROM schema_meta ORDER BY version"),
            (
                "bundle_manifests",
                "SELECT id, digest, release_digest FROM bundle_manifests ORDER BY id",
            ),
            ("nodes", "SELECT id, digest FROM nodes ORDER BY id"),
            ("claims", "SELECT id, digest FROM claims ORDER BY id"),
            ("evidence", "SELECT id, digest FROM evidence ORDER BY id"),
            (
                "evidence_attestations",
                """SELECT evidence_id, nonce, digest FROM evidence_attestations
                   ORDER BY evidence_id""",
            ),
            (
                "evidence_receipts",
                "SELECT evidence_id, nonce, digest FROM evidence_receipts ORDER BY evidence_id",
            ),
            ("mistakes", "SELECT id, digest FROM mistakes ORDER BY id"),
            ("reflexes", "SELECT id, digest FROM reflexes ORDER BY id"),
            (
                "reflex_sources",
                """SELECT reflex_id, mistake_id FROM reflex_sources
                   ORDER BY reflex_id, mistake_id""",
            ),
            (
                "verification_events",
                "SELECT id, digest FROM verification_events ORDER BY id",
            ),
            (
                "verification_event_evidence",
                """SELECT event_id, evidence_id, ordinal
                   FROM verification_event_evidence
                   ORDER BY event_id, ordinal""",
            ),
            ("edges", "SELECT id, digest FROM edges ORDER BY id"),
            (
                "bundle_members",
                """SELECT bundle_id, entry_key, node_id FROM bundle_members
                   ORDER BY bundle_id, entry_key""",
            ),
        )
        if self.has_edge_retractions() and self.connection.execute(
            "SELECT 1 FROM edge_retractions LIMIT 1"
        ).fetchone():
            queries += (
                (
                    "edge_retractions",
                    "SELECT edge_id, digest FROM edge_retractions ORDER BY edge_id",
                ),
            )
        digest = hashlib.sha256(b"math-brain-store-state-v1\n")
        for table, query in queries:
            digest.update(table.encode("utf-8"))
            digest.update(b"\0")
            for row in self.connection.execute(query):
                digest.update(canonical_json(dict(row)).encode("utf-8"))
                digest.update(b"\n")
        return "sha256:" + digest.hexdigest()

    def get_bundle_manifest(self, bundle_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT digest, payload_json FROM bundle_manifests WHERE id = ?", (bundle_id,)
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError("stored bundle manifest is not a JSON object")
        if content_hash(payload) != row["digest"]:
            raise ValueError("stored bundle manifest digest does not match its payload")
        return payload

    def get_nodes(self, node_ids: Iterable[str]) -> dict[str, GraphNode]:
        """Return existing nodes keyed by id without exceeding SQLite bind limits."""

        ids = tuple(dict.fromkeys(node_ids))
        result: dict[str, GraphNode] = {}
        for offset in range(0, len(ids), 500):
            chunk = ids[offset : offset + 500]
            placeholders = ",".join("?" for _ in chunk)
            rows = self.connection.execute(
                f"SELECT * FROM nodes WHERE id IN ({placeholders})", chunk
            ).fetchall()
            for row in rows:
                result[row["id"]] = GraphNode(
                    id=row["id"],
                    kind=NodeKind(row["kind"]),
                    title=row["title"],
                    body=row["body"],
                    metadata=json.loads(row["metadata_json"]),
                    created_at=row["created_at"],
                )
        return result

    def evidence_for(self, claim_id: str) -> list[Evidence]:
        rows = self.connection.execute(
            """SELECT id, claim_id, digest, created_at, payload_json FROM evidence
               WHERE claim_id = ? ORDER BY created_at, id""",
            (claim_id,),
        ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["kind"] = EvidenceKind(payload["kind"])
            payload["limitations"] = tuple(payload["limitations"])
            evidence = Evidence(**payload)
            if (
                evidence.id != row["id"]
                or evidence.claim_id != row["claim_id"]
                or evidence.created_at != row["created_at"]
                or evidence.digest() != row["digest"]
            ):
                raise ValueError("stored evidence columns do not match its payload")
            result.append(evidence)
        return result

    def evidence_receipts_for(self, claim_id: str) -> dict[str, EvidenceReceipt]:
        rows = self.connection.execute(
            """SELECT evidence_id, claim_id, digest, created_at, payload_json
               FROM evidence_receipts
               WHERE claim_id = ? ORDER BY created_at, evidence_id""",
            (claim_id,),
        ).fetchall()
        result: dict[str, EvidenceReceipt] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["evidence_kind"] = EvidenceKind(payload["evidence_kind"])
            payload["authority_role"] = EvidenceAuthorityRole(payload["authority_role"])
            receipt = EvidenceReceipt(**payload)
            if (
                receipt.evidence_id != row["evidence_id"]
                or receipt.claim_id != row["claim_id"]
                or receipt.created_at != row["created_at"]
                or receipt.digest() != row["digest"]
            ):
                raise ValueError("stored evidence receipt columns do not match its payload")
            result[receipt.evidence_id] = receipt
        return result

    def evidence_attestations_for(
        self, claim_id: str
    ) -> dict[str, EvidenceAttestation]:
        rows = self.connection.execute(
            """SELECT evidence_id, claim_id, digest, created_at, payload_json
               FROM evidence_attestations
               WHERE claim_id = ? ORDER BY created_at, evidence_id""",
            (claim_id,),
        ).fetchall()
        result: dict[str, EvidenceAttestation] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["evidence_kind"] = EvidenceKind(payload["evidence_kind"])
            payload["authority_role"] = EvidenceAuthorityRole(payload["authority_role"])
            payload["authority_mode"] = EvidenceAuthorityMode(payload["authority_mode"])
            attestation = EvidenceAttestation(**payload)
            if (
                attestation.evidence_id != row["evidence_id"]
                or attestation.claim_id != row["claim_id"]
                or attestation.created_at != row["created_at"]
                or attestation.digest() != row["digest"]
            ):
                raise ValueError(
                    "stored evidence attestation columns do not match its payload"
                )
            result[attestation.evidence_id] = attestation
        return result

    def verification_events_for(self, claim_id: str) -> list[VerificationEvent]:
        rows = self.connection.execute(
            """SELECT id, claim_id, digest, created_at, payload_json
               FROM verification_events
               WHERE claim_id = ? ORDER BY created_at, rowid""",
            (claim_id,),
        ).fetchall()
        result: list[VerificationEvent] = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload["from_status"] = RecordStatus(payload["from_status"])
            payload["to_status"] = RecordStatus(payload["to_status"])
            payload["evidence_ids"] = tuple(payload["evidence_ids"])
            event = VerificationEvent(**payload)
            if (
                event.id != row["id"]
                or event.claim_id != row["claim_id"]
                or event.created_at != row["created_at"]
                or event.digest() != row["digest"]
            ):
                raise ValueError("stored verification event columns do not match its payload")
            joined = self.connection.execute(
                """SELECT evidence_id FROM verification_event_evidence
                   WHERE event_id = ? ORDER BY ordinal""",
                (row["id"],),
            ).fetchall()
            if tuple(item["evidence_id"] for item in joined) != event.evidence_ids:
                raise ValueError("verification event evidence foreign keys do not match payload")
            result.append(event)
        return result

    def get_mistake(self, mistake_id: str) -> MistakeRecord | None:
        """Return the stored mistake bound to its row, or raise ValueError.

        The record rebuilt from the payload must carry the row's id, claim id and creation time
        and hash to the row's digest. The graph node is not compared: a mistake may annotate its
        claim's own node (the scar layer's backfill writes no mistake node), and nodes are checked
        by the note reader.
        """

        row = self.connection.execute(
            """SELECT id, claim_id, digest, created_at, payload_json FROM mistakes
               WHERE id = ?""",
            (mistake_id,),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        payload["kind"] = MistakeKind(payload["kind"])
        payload["repair_steps"] = tuple(payload["repair_steps"])
        payload["affected_assumptions"] = tuple(payload["affected_assumptions"])
        mistake = MistakeRecord(**payload)
        if (
            mistake.id != row["id"]
            or mistake.claim_id != row["claim_id"]
            or mistake.created_at != row["created_at"]
            or mistake.digest() != row["digest"]
        ):
            raise ValueError("stored mistake columns do not match its payload")
        return mistake

    def get_reflex(self, reflex_id: str) -> ReflexRecord | None:
        """Return the stored reflex bound to its row and its source rows, or raise ValueError.

        As for mistakes, and the payload's source mistake ids must be exactly the reflex's rows in
        reflex_sources.
        """

        row = self.connection.execute(
            "SELECT id, digest, created_at, payload_json FROM reflexes WHERE id = ?",
            (reflex_id,),
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        for field_name in (
            "activation_cues",
            "action_steps",
            "required_evidence",
            "source_mistake_ids",
        ):
            payload[field_name] = tuple(payload[field_name])
        reflex = ReflexRecord(**payload)
        if (
            reflex.id != row["id"]
            or reflex.created_at != row["created_at"]
            or reflex.digest() != row["digest"]
        ):
            raise ValueError("stored reflex columns do not match its payload")
        joined = sorted(
            item["mistake_id"]
            for item in self.connection.execute(
                "SELECT mistake_id FROM reflex_sources WHERE reflex_id = ?", (row["id"],)
            )
        )
        if joined != sorted(reflex.source_mistake_ids):
            raise ValueError("stored reflex sources do not match its payload")
        return reflex

    def current_status(self, claim_id: str) -> RecordStatus:
        claim = self.get_claim(claim_id)
        if claim is None:
            raise KeyError(claim_id)
        row = self.connection.execute(
            "SELECT 1 FROM verification_events WHERE claim_id = ? LIMIT 1",
            (claim_id,),
        ).fetchone()
        if row is not None:
            raise RuntimeError(
                "persisted status must be recomputed from authenticated evidence"
            )
        if claim.status is not RecordStatus.UNVERIFIED:
            raise ValueError("persisted claim has an invalid caller-authored initial status")
        return claim.status

    def iter_nodes(self) -> Iterator[GraphNode]:
        for row in self.connection.execute("SELECT * FROM nodes ORDER BY created_at, id"):
            yield GraphNode(
                id=row["id"],
                kind=NodeKind(row["kind"]),
                title=row["title"],
                body=row["body"],
                metadata=json.loads(row["metadata_json"]),
                created_at=row["created_at"],
            )

    def unknown_edge_kinds(self) -> dict[str, int]:
        """Edge kinds in the store that this build's EdgeKind does not define, with row counts."""
        known = {member.value for member in EdgeKind}
        rows = self.connection.execute(
            "SELECT kind, COUNT(*) AS n FROM edges GROUP BY kind ORDER BY kind"
        ).fetchall()
        return {str(row["kind"]): int(row["n"]) for row in rows if row["kind"] not in known}

    def iter_edges(
        self,
        *,
        kind: EdgeKind | None = None,
        skip_unknown: bool = False,
        include_retracted: bool = False,
    ) -> Iterator[GraphEdge]:
        """Yield edges; an unknown kind raises UnknownEdgeKindError unless skip_unknown is set,
        in which case the rows are skipped and one UnknownEdgeKindWarning names the kind.
        Retracted edges are skipped unless include_retracted is set."""
        retracted = frozenset() if include_retracted else self.retracted_edge_ids()
        if kind is None:
            rows = self.connection.execute("SELECT * FROM edges ORDER BY created_at, id")
        else:
            rows = self.connection.execute(
                "SELECT * FROM edges WHERE kind = ? ORDER BY created_at, id",
                (kind.value,),
            )
        warned: set[str] = set()
        for row in rows:
            if row["id"] in retracted:
                continue
            edge = self._edge_from_row(row, skip_unknown=skip_unknown, warned=warned)
            if edge is not None:
                yield edge

    def neighbors(
        self,
        node_ids: Iterable[str],
        *,
        skip_unknown: bool = False,
        include_retracted: bool = False,
    ) -> list[GraphEdge]:
        ids = tuple(dict.fromkeys(node_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        query = (
            f"SELECT * FROM edges WHERE source_id IN ({placeholders}) "
            f"OR target_id IN ({placeholders})"
        )
        rows = self.connection.execute(query, ids + ids).fetchall()
        retracted = frozenset() if include_retracted else self.retracted_edge_ids()
        warned: set[str] = set()
        result = []
        for row in rows:
            if row["id"] in retracted:
                continue
            edge = self._edge_from_row(row, skip_unknown=skip_unknown, warned=warned)
            if edge is not None:
                result.append(edge)
        return result
