"""Where a claim came from, and whether anybody checked it.

The population store holds 1,130 claims and zero rows in `evidence`,
`evidence_attestations`, `evidence_receipts`, `verification_events` and
`mistakes`. A consumer therefore cannot tell a theorem somebody proved from a
sentence lifted off a Wikipedia lead from a working note a campaign left behind -
they arrive in the same shape, with the same authority, and 96.4% of them carry
the single status string "unverified".

This module adds the missing layer in the two places it is useful:

  classify(node)        a pure function over the metadata already on the node, so
                        any consumer holding a GraphNode can label it without a
                        database round trip. Returns an EvidenceClass and the
                        fields the label was read from.

  claim_provenance      an additive table in the same store, one row per claim,
                        holding the resolved record. Nothing existing is modified;
                        the table can be dropped without touching a claim.

The classes say what KIND of support a claim has, never how true it is:

  MEASURED    a numeric or computational check is recorded for this claim
  DERIVED     derived inside a campaign; REFEREED when a named referee report exists
  REPLACED    a verifier-approved replacement for an earlier claim
  HARVESTED   copied from a reference source (Wikipedia, ProofWiki, arXiv). The
              source is real; nobody in this store checked the transcription
  ASSERTED    authored into the store with no source and no check recorded
  UNKNOWN     the metadata does not say, and guessing would be worse than silence

UNKNOWN is a legitimate answer and is used wherever provenance is genuinely not
recoverable. A confident label on an unlabelled claim is the failure this layer
exists to prevent.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable


class EvidenceClass(StrEnum):
    MEASURED = "measured"
    DERIVED = "derived"
    REPLACED = "replaced"
    HARVESTED = "harvested"
    ASSERTED = "asserted"
    UNKNOWN = "unknown"


#: classes a consumer may treat as "somebody checked this in this store"
CHECKED = frozenset({EvidenceClass.MEASURED, EvidenceClass.DERIVED, EvidenceClass.REPLACED})

SOURCE_PREFIXES = {
    "wikipedia": "wikipedia",
    "proofwiki": "proofwiki",
    "arxiv": "arxiv",
    "campaign": "campaign",
}
_FIRE_RE = re.compile(r"\bfires?\s*([0-9]+(?:\s*-\s*[0-9]+)?)", re.IGNORECASE)
_REFEREE_RE = re.compile(r"referee|refereed|REFEREE\d*_[\w.]+", re.IGNORECASE)
_MEASURE_RE = re.compile(
    r"numeric check|rel\.?\s*diff|re-derived|recomputed|verified\s+\d|"
    r"computation|simulation|measured",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Provenance:
    claim_id: str
    evidence_class: EvidenceClass
    source_kind: str = ""          # wikipedia | proofwiki | arxiv | campaign | ""
    source_ref: str = ""           # the source identifier as recorded
    artifact_path: str = ""        # the bundle entry the claim was ingested from
    bundle_id: str = ""
    fire_id: str = ""              # campaign fire this claim was deposited by
    measurement: str = ""          # the free-text provenance note, verbatim
    status: str = ""               # the claim's own status string
    read_from: tuple[str, ...] = field(default=())   # metadata keys the label used

    @property
    def checked(self) -> bool:
        return self.evidence_class in CHECKED


def _meta(node) -> dict:
    meta = getattr(node, "metadata", None)
    return meta if isinstance(meta, dict) else {}


def classify(node, *, status: str = "") -> Provenance:
    """Label one node from the metadata it already carries. No I/O, no guessing."""

    meta = _meta(node)
    used: list[str] = []

    def take(key: str) -> str:
        value = meta.get(key)
        if value in (None, "", [], {}):
            return ""
        used.append(key)
        return str(value)

    source_ref = take("source_id")
    source_kind = ""
    if source_ref:
        head = source_ref.split(":", 1)[0].strip().casefold()
        source_kind = SOURCE_PREFIXES.get(head, "")
    note = take("provenance") or take("source")
    artifact = take("artifact_path") or take("draft")
    bundle = take("bundle_id")
    claim_status = (status or str(meta.get("truth_status") or "")).strip().casefold()
    if claim_status:
        used.append("status")

    title = str(getattr(node, "title", "") or "")
    fire_match = _FIRE_RE.search(title) or _FIRE_RE.search(note)
    fire_id = re.sub(r"\s+", "", fire_match.group(1)) if fire_match else ""

    # Order matters: the strongest recorded support wins, and a source alone is
    # never treated as a check.
    if note and _MEASURE_RE.search(note):
        klass = EvidenceClass.MEASURED
    elif claim_status.startswith("derived_in_campaign") or (note and _REFEREE_RE.search(note)):
        klass = EvidenceClass.DERIVED
    elif claim_status in {"verified_replacement", "verified"} or "replacement" in note.casefold():
        klass = EvidenceClass.REPLACED
    elif source_kind in {"wikipedia", "proofwiki", "arxiv"}:
        klass = EvidenceClass.HARVESTED
    elif source_kind == "campaign" or fire_id:
        klass = EvidenceClass.DERIVED
    elif artifact or bundle or note:
        klass = EvidenceClass.ASSERTED
    else:
        klass = EvidenceClass.UNKNOWN

    return Provenance(
        claim_id=str(getattr(node, "id", "")),
        evidence_class=klass,
        source_kind=source_kind,
        source_ref=source_ref,
        artifact_path=artifact,
        bundle_id=bundle,
        fire_id=fire_id,
        measurement=note,
        status=claim_status,
        read_from=tuple(dict.fromkeys(used)),
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS claim_provenance (
    claim_id       TEXT PRIMARY KEY,
    evidence_class TEXT NOT NULL,
    source_kind    TEXT NOT NULL DEFAULT '',
    source_ref     TEXT NOT NULL DEFAULT '',
    artifact_path  TEXT NOT NULL DEFAULT '',
    bundle_id      TEXT NOT NULL DEFAULT '',
    fire_id        TEXT NOT NULL DEFAULT '',
    measurement    TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT '',
    read_from      TEXT NOT NULL DEFAULT '[]',
    recorded_at    TEXT NOT NULL
)
"""
INDEX = "CREATE INDEX IF NOT EXISTS claim_provenance_class ON claim_provenance(evidence_class)"


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(SCHEMA)
    connection.execute(INDEX)


def write(connection: sqlite3.Connection, records: Iterable[Provenance], recorded_at: str) -> int:
    ensure_schema(connection)
    rows = [
        (
            item.claim_id, str(item.evidence_class), item.source_kind, item.source_ref,
            item.artifact_path, item.bundle_id, item.fire_id, item.measurement,
            item.status, json.dumps(list(item.read_from)), recorded_at,
        )
        for item in records
    ]
    connection.executemany(
        "INSERT OR REPLACE INTO claim_provenance (claim_id, evidence_class, source_kind,"
        " source_ref, artifact_path, bundle_id, fire_id, measurement, status, read_from,"
        " recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def read(connection: sqlite3.Connection, claim_id: str) -> Provenance | None:
    ensure_schema(connection)
    row = connection.execute(
        "SELECT claim_id, evidence_class, source_kind, source_ref, artifact_path,"
        " bundle_id, fire_id, measurement, status, read_from"
        " FROM claim_provenance WHERE claim_id = ?",
        (claim_id,),
    ).fetchone()
    if row is None:
        return None
    return Provenance(
        claim_id=row[0], evidence_class=EvidenceClass(row[1]), source_kind=row[2],
        source_ref=row[3], artifact_path=row[4], bundle_id=row[5], fire_id=row[6],
        measurement=row[7], status=row[8], read_from=tuple(json.loads(row[9])),
    )


def short_label(item: Provenance | None) -> str:
    """One field a consumer can print beside a claim without explaining the schema."""

    if item is None:
        return "provenance unknown"
    if item.evidence_class is EvidenceClass.HARVESTED and item.source_ref:
        return "harvested from %s" % item.source_ref
    if item.evidence_class is EvidenceClass.DERIVED:
        if item.fire_id:
            return "derived in campaign, fire %s" % item.fire_id
        return "derived in campaign: %s" % (item.measurement[:80] or item.status or "no note")
    if item.evidence_class is EvidenceClass.MEASURED:
        return "measured: %s" % (item.measurement[:80] or "check recorded")
    if item.evidence_class is EvidenceClass.REPLACED:
        return "verifier-approved replacement"
    if item.evidence_class is EvidenceClass.ASSERTED:
        return "asserted, no check recorded"
    return "provenance unknown"
