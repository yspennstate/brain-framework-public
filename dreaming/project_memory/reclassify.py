"""Source-verified, signed, two-phase project classification rebuilds."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .clauses import project_prompt
from .classify import (
    CLASSIFIER_VERSION,
    LEGACY_CLASSIFIER_VERSION,
    UNKNOWN_PROJECT,
    classification_digest,
    classification_digest_for_version,
    classify_clause,
    load_config,
)
from .hot_cache import build_contract_cache, cache_path, load_contract_cache
from .sources import (
    commit_manifests,
    hydrate_event_rows,
    scan_sources,
    source_descriptors,
)
from .store import AUTHORITATIVE_EVENT_SQL, ProjectMemoryStore, utc_now

ARTIFACT_SCHEMA = "project-classification-rebuild-v1"
CANDIDATE_SCHEMA = "project-classification-candidate-v1"
INPUT_DIGEST_SCHEMA = "project-classification-input-v1"
NONAUTHORITATIVE_OWNER_SQL = (
    "p.provenance='owner' AND (p.source_id='hook' "
    "OR p.source_content_hmac IS NULL "
    "OR p.source_content_hmac<>p.content_hmac OR p.projection_version<5)"
)
Progress = Callable[[str], None] | None


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-%d" % os.getpid())
    with open(temporary, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _report(progress: Progress, message: str) -> None:
    if progress is not None:
        progress(message)


def _inside_private_root(store: ProjectMemoryStore, path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(store.root)
    except ValueError as exc:
        raise RuntimeError("classification_artifact_must_be_private") from exc
    return resolved


def _hash_query(digest, store: ProjectMemoryStore, label: str, sql: str,
                parameters: tuple = ()) -> None:
    digest.update(("\n--" + label + "--\n").encode("ascii"))
    for row in store.db.execute(sql, parameters):
        digest.update(_canonical(list(row)))
        digest.update(b"\n")


def classification_input_digest(store: ProjectMemoryStore) -> str:
    """Commit to every DB input that can affect the candidate or quarantine."""
    digest = hashlib.sha256((INPUT_DIGEST_SCHEMA + "\n").encode("ascii"))
    owner_scope = "(" + AUTHORITATIVE_EVENT_SQL + ") OR (" + NONAUTHORITATIVE_OWNER_SQL + ")"
    _hash_query(
        digest, store, "events",
        "SELECT p.ingest_seq,p.event_id,p.source_id,p.source_seq,p.source_ref_hmac,"
        "p.content_hmac,p.source_content_hmac,p.occurred_at,p.session_hmac,p.provenance,"
        "p.runtime,p.span_count,p.clause_count,p.parser_overflow,p.projection_version "
        "FROM prompt_events p WHERE " + owner_scope + " ORDER BY p.ingest_seq",
    )
    _hash_query(
        digest, store, "spans",
        "SELECT s.span_id,s.event_id,s.ordinal,s.start_offset,s.end_offset,s.span_kind,s.span_hmac "
        "FROM coverage_spans s JOIN prompt_events p ON p.event_id=s.event_id WHERE " +
        AUTHORITATIVE_EVENT_SQL + " ORDER BY s.event_id,s.ordinal",
    )
    _hash_query(
        digest, store, "span-dispositions",
        "SELECT d.span_id,d.event_id,d.disposition,d.reason_code FROM coverage_dispositions d "
        "JOIN prompt_events p ON p.event_id=d.event_id WHERE " + AUTHORITATIVE_EVENT_SQL +
        " ORDER BY d.event_id,d.span_id",
    )
    _hash_query(
        digest, store, "clauses",
        "SELECT c.clause_id,c.event_id,c.span_id,c.ordinal,c.role,c.weight,c.features_json,"
        "c.exact_categories_json,c.forbidden_ops_json,c.actionable FROM clauses c "
        "JOIN prompt_events p ON p.event_id=c.event_id WHERE " + AUTHORITATIVE_EVENT_SQL +
        " ORDER BY c.event_id,c.ordinal",
    )
    _hash_query(
        digest, store, "source-manifests",
        "SELECT sm.source_id,sm.size_bytes,sm.line_count,sm.prefix_hmac,sm.cursor_seq,"
        "sm.mtime_ns FROM source_manifests sm WHERE sm.source_id IN "
        "(SELECT DISTINCT p.source_id FROM prompt_events p WHERE " +
        AUTHORITATIVE_EVENT_SQL + ") ORDER BY sm.source_id",
    )
    return digest.hexdigest()


def assignment_table_digest(store: ProjectMemoryStore) -> str:
    digest = hashlib.sha256(b"project-classification-assignments-v1\n")
    _hash_query(
        digest, store, "assignments",
        "SELECT revision_id,clause_id,project_id,score,confidence_band,"
        "evidence_codes_json,classifier_version,created_at "
        "FROM clause_assignments ORDER BY clause_id,project_id,classifier_version",
    )
    return digest.hexdigest()


def _candidate_assignment_digest(candidate: sqlite3.Connection) -> str:
    digest = hashlib.sha256(b"project-classification-candidate-assignments-v1\n")
    for row in candidate.execute(
        "SELECT revision_id,clause_id,project_id,score,confidence_band,"
        "evidence_codes_json,classifier_version,created_at "
        "FROM candidate_assignments ORDER BY clause_id,project_id"
    ):
        digest.update(_canonical(list(row)))
        digest.update(b"\n")
    return digest.hexdigest()


def _applied_candidate_assignment_digest(store: ProjectMemoryStore) -> str:
    digest = hashlib.sha256(
        b"project-classification-candidate-assignments-v1\n"
    )
    for row in store.db.execute(
        "SELECT revision_id,clause_id,project_id,score,confidence_band,"
        "evidence_codes_json,classifier_version,created_at "
        "FROM clause_assignments ORDER BY clause_id,project_id"
    ):
        digest.update(_canonical(list(row)))
        digest.update(b"\n")
    return digest.hexdigest()


def _stored_classifier_version(store: ProjectMemoryStore) -> str:
    row = store.db.execute(
        "SELECT value FROM schema_meta WHERE key='classification_classifier_version'"
    ).fetchone()
    if row:
        return str(row[0])
    versions = {
        str(item[0]) for item in store.db.execute(
            "SELECT DISTINCT classifier_version FROM clause_assignments"
        )
    }
    if len(versions) > 1:
        raise RuntimeError("multiple_live_classifier_versions_require_manual_audit")
    if versions:
        return versions.pop()
    return LEGACY_CLASSIFIER_VERSION


def _projection_rows(store: ProjectMemoryStore, event_id: str, text: str
                     ) -> tuple[list[dict], bool]:
    def scoped_keyed(domain: str, value: str) -> str:
        if domain in {"span", "span-bytes", "clause"}:
            value = event_id + "\x1f" + value
        return store.keyed(domain, value)

    spans, clauses, overflow = project_prompt(text, scoped_keyed)
    expected_spans = [
        (span["span_id"], event_id, int(span["ordinal"]), int(span["start"]),
         int(span["end"]), span["kind"], span["span_hmac"])
        for span in spans
    ]
    actual_spans = [
        tuple(row) for row in store.db.execute(
            "SELECT span_id,event_id,ordinal,start_offset,end_offset,span_kind,span_hmac "
            "FROM coverage_spans WHERE event_id=? ORDER BY ordinal", (event_id,)
        )
    ]
    if actual_spans != expected_spans:
        raise RuntimeError("projection_span_drift:" + event_id)
    expected_clauses = [
        (clause["clause_id"], event_id, clause["span_id"], int(clause["ordinal"]),
         clause["role"], float(clause["weight"]), list(clause["features"]),
         list(clause["exact_categories"]), list(clause["forbidden_ops"]),
         int(clause["actionable"]))
        for clause in clauses
    ]
    actual_clauses = [
        (str(row["clause_id"]), str(row["event_id"]), str(row["span_id"]),
         int(row["ordinal"]), str(row["role"]), float(row["weight"]),
         json.loads(row["features_json"]), json.loads(row["exact_categories_json"]),
         json.loads(row["forbidden_ops_json"]), int(row["actionable"]))
        for row in store.db.execute(
            "SELECT clause_id,event_id,span_id,ordinal,role,weight,features_json,"
            "exact_categories_json,forbidden_ops_json,actionable FROM clauses "
            "WHERE event_id=? ORDER BY ordinal", (event_id,)
        )
    ]
    if actual_clauses != expected_clauses:
        raise RuntimeError("projection_clause_drift:" + event_id)
    clause_spans = {str(clause["span_id"]) for clause in clauses}
    expected_dispositions = sorted(
        (str(span["span_id"]),
         "non_actionable_separator" if span["kind"] == "separator"
         else "retained_overflow" if span["kind"] == "unparsed_overflow"
         else "retained_unparsed",
         "no_semantic_clause")
        for span in spans if str(span["span_id"]) not in clause_spans
    )
    actual_dispositions = [
        tuple(row) for row in store.db.execute(
            "SELECT span_id,disposition,reason_code FROM coverage_dispositions "
            "WHERE event_id=? ORDER BY span_id", (event_id,)
        )
    ]
    if actual_dispositions != expected_dispositions:
        raise RuntimeError("projection_disposition_drift:" + event_id)
    return clauses, bool(overflow)


def _verify_source_event(store: ProjectMemoryStore, event, record: dict) -> list[dict]:
    event_id = str(event["event_id"])
    text = record.get("text")
    if not isinstance(text, str):
        raise RuntimeError("source_text_missing:" + event_id)
    content_hmac = store.keyed("prompt-content", text)
    if (not hmac.compare_digest(content_hmac, str(event["content_hmac"])) or
            not hmac.compare_digest(content_hmac, str(event["source_content_hmac"]))):
        raise RuntimeError("source_content_hmac_mismatch:" + event_id)
    if str(record.get("provenance") or "") != "owner":
        raise RuntimeError("source_provenance_not_owner:" + event_id)
    if store.session_hmac(str(record.get("session") or "")) != str(event["session_hmac"]):
        raise RuntimeError("source_session_mismatch:" + event_id)
    if str(record.get("occurred_at") or "") != str(event["occurred_at"]):
        raise RuntimeError("source_timestamp_mismatch:" + event_id)
    if str(record.get("runtime") or "") != str(event["runtime"]):
        raise RuntimeError("source_runtime_mismatch:" + event_id)
    clauses, overflow = _projection_rows(store, event_id, text)
    if (len(clauses) != int(event["clause_count"]) or
            bool(int(event["parser_overflow"])) != overflow):
        raise RuntimeError("projection_count_drift:" + event_id)
    return clauses


def _legacy_authority_candidates(store: ProjectMemoryStore) -> list:
    """Return inert or unsafe legacy rows that an exact source may re-prove."""
    return list(store.db.execute(
        "SELECT DISTINCT p.ingest_seq,p.event_id,p.source_id,p.source_seq,"
        "p.source_ref_hmac,p.content_hmac,p.source_content_hmac,p.occurred_at,"
        "p.session_hmac,p.provenance,p.runtime,p.projection_version "
        "FROM prompt_events p WHERE p.source_id<>'hook' "
        "AND p.source_id NOT LIKE 'authority-quarantine:%' AND NOT (" +
        AUTHORITATIVE_EVENT_SQL + ") AND (p.provenance IN ('owner','unknown') "
        "OR EXISTS (SELECT 1 FROM clauses c JOIN clause_assignments ca "
        "ON ca.clause_id=c.clause_id WHERE c.event_id=p.event_id)) "
        "ORDER BY p.source_id,p.source_seq,p.event_id"
    ))


def _reconcile_legacy_authority(
        store: ProjectMemoryStore, ingest_config: dict,
        progress: Progress = None) -> dict[str, int]:
    """Re-prove legacy rows from exact configured sources, never from DB labels."""
    candidates = _legacy_authority_candidates(store)
    stats = {
        "legacy_candidates": len(candidates),
        "legacy_resolved": 0,
        "legacy_owner_verified": 0,
        "legacy_non_owner": 0,
        "legacy_unavailable": 0,
        "legacy_inserted": 0,
        "legacy_existing": 0,
    }
    if not candidates:
        return stats
    needed_source_ids = {
        str(row["source_id"]) for row in candidates
    }
    descriptors = source_descriptors(
        store, ingest_config, needed_source_ids, require_all=False
    )
    available = [
        row for row in candidates
        if str(row["source_id"]) in descriptors
    ]
    stats["legacy_unavailable"] = len(candidates) - len(available)
    if not available:
        return stats
    hydrated = hydrate_event_rows(
        store, ingest_config, available, descriptors=descriptors
    )
    stats["legacy_resolved"] = len(available)
    for row in available:
        key = (str(row["source_id"]), int(row["source_seq"]))
        record = hydrated[key]
        if str(record.get("provenance") or "") != "owner":
            stats["legacy_non_owner"] += 1
            continue
        stats["legacy_owner_verified"] += 1
        _event_id, created = store.ingest_prompt(
            str(record["text"]), source_id=key[0], source_seq=key[1],
            session=str(record.get("session") or ""),
            occurred_at=str(record.get("occurred_at") or ""),
            provenance="owner", runtime=str(record.get("runtime") or ""),
            cwd=str(record.get("cwd") or ""),
            source_project=str(record.get("source_project") or ""),
            config=ingest_config, classify_assignments=False,
            reconcile_existing_authority=True,
        )
        stats["legacy_inserted"] += int(created)
        stats["legacy_existing"] += int(not created)
    _report(
        progress,
        "legacy authority reconciliation candidates=%d owner=%d "
        "non_owner=%d unavailable=%d" %
        (stats["legacy_candidates"], stats["legacy_owner_verified"],
         stats["legacy_non_owner"], stats["legacy_unavailable"]),
    )
    return stats


def _reconcile_sources(store: ProjectMemoryStore, target_config: dict,
                       prior_config: dict, progress: Progress = None
                       ) -> dict[str, int]:
    ingest_config = dict(prior_config)
    for key in ("sources", "replay_sources", "source_ingest_budget_bytes"):
        ingest_config[key] = target_config.get(key)
    _report(progress, "scanning configured authoritative sources")
    rows, _source_rows, pending_manifests = scan_sources(store, ingest_config)
    _report(progress, "source scan complete rows=%d" % len(rows))
    inserted = existing = 0
    for row in rows:
        _event_id, created = store.ingest_prompt(
            row.text, source_id=row.source_id, source_seq=row.source_seq,
            session=row.session, occurred_at=row.occurred_at,
            provenance=row.provenance, runtime=row.runtime, cwd=row.cwd,
            source_project=row.source_project, config=ingest_config,
            classify_assignments=False,
            reconcile_existing_authority=True,
        )
        inserted += int(created)
        existing += int(not created)
    commit_manifests(store, pending_manifests)
    legacy = _reconcile_legacy_authority(
        store, ingest_config, progress
    )
    return {
        "inserted": inserted + legacy["legacy_inserted"],
        "existing": existing + legacy["legacy_existing"],
        "incremental_rows": len(rows),
        **legacy,
    }


def _create_candidate(store: ProjectMemoryStore, config: dict, target: Path,
                      prepared_at: str, progress: Progress = None) -> dict:
    source_ids = {
        str(row[0]) for row in store.db.execute(
            "SELECT DISTINCT p.source_id FROM prompt_events p WHERE " +
            AUTHORITATIVE_EVENT_SQL
        )
    }
    descriptors = source_descriptors(store, config, source_ids)
    candidate = sqlite3.connect(str(target))
    candidate.row_factory = sqlite3.Row
    candidate.execute("PRAGMA journal_mode=DELETE")
    candidate.execute("PRAGMA synchronous=FULL")
    candidate.executescript(
        "CREATE TABLE artifact_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
        "CREATE TABLE candidate_assignments("
        "revision_id TEXT PRIMARY KEY,clause_id TEXT NOT NULL,project_id TEXT NOT NULL,"
        "score REAL NOT NULL,confidence_band TEXT NOT NULL,evidence_codes_json TEXT NOT NULL,"
        "classifier_version TEXT NOT NULL,created_at TEXT NOT NULL,"
        "UNIQUE(clause_id,project_id));"
        "CREATE INDEX candidate_clause ON candidate_assignments(clause_id);"
        "CREATE INDEX candidate_project ON candidate_assignments(project_id);"
    )
    eligible_events = eligible_clauses = 0
    try:
        sessions = [
            str(row[0]) for row in store.db.execute(
                "SELECT DISTINCT p.session_hmac FROM prompt_events p WHERE " +
                AUTHORITATIVE_EVENT_SQL + " ORDER BY p.session_hmac"
            )
        ]
        _report(progress, "candidate classification sessions=%d" % len(sessions))
        report_every = max(1, len(sessions) // 20)
        for session_index, session_hmac in enumerate(sessions, 1):
            events = list(store.db.execute(
                "SELECT p.event_id,p.source_id,p.source_seq,p.content_hmac,"
                "p.source_content_hmac,p.occurred_at,p.session_hmac,p.provenance,p.runtime,"
                "p.span_count,p.clause_count,p.parser_overflow,p.projection_version "
                "FROM prompt_events p WHERE p.session_hmac=? AND " +
                AUTHORITATIVE_EVENT_SQL +
                " ORDER BY p.occurred_at,p.source_id,p.source_seq,p.event_id",
                (session_hmac,),
            ))
            hydrated = hydrate_event_rows(
                store, config, events, descriptors=descriptors
            )
            prior_events: deque[set[str]] = deque(maxlen=4)
            for event in events:
                event_id = str(event["event_id"])
                key = (str(event["source_id"]), int(event["source_seq"]))
                record = hydrated[key]
                clauses = _verify_source_event(store, event, record)
                session_prior = tuple(sorted(
                    set().union(*prior_events) if prior_events else set()
                ))
                event_projects: set[str] = set()
                for clause in clauses:
                    assignments = classify_clause(
                        clause["raw"], cwd=str(record.get("cwd") or ""),
                        source_project=str(record.get("source_project") or ""),
                        session_prior=session_prior, config=config,
                    )
                    if not assignments:
                        raise RuntimeError("candidate_clause_unassigned:" + clause["clause_id"])
                    for assignment in assignments:
                        revision = "assign:" + store.keyed(
                            "assignment", "%s:%s:%s" %
                            (clause["clause_id"], assignment["project_id"],
                             CLASSIFIER_VERSION)
                        )[:32]
                        candidate.execute(
                            "INSERT INTO candidate_assignments(revision_id,clause_id,"
                            "project_id,score,confidence_band,evidence_codes_json,"
                            "classifier_version,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (revision, clause["clause_id"], assignment["project_id"],
                             assignment["score"], assignment["confidence_band"],
                             json.dumps(assignment["evidence_codes"],
                                        separators=(",", ":")),
                             CLASSIFIER_VERSION, prepared_at),
                        )
                        if assignment["project_id"] != UNKNOWN_PROJECT:
                            event_projects.add(str(assignment["project_id"]))
                    eligible_clauses += 1
                prior_events.append(event_projects)
                eligible_events += 1
            if (session_index == 1 or session_index == len(sessions) or
                    session_index % report_every == 0):
                _report(
                    progress,
                    "candidate progress sessions=%d/%d events=%d clauses=%d" %
                    (session_index, len(sessions), eligible_events,
                     eligible_clauses),
                )
        assignment_count = int(candidate.execute(
            "SELECT COUNT(*) FROM candidate_assignments"
        ).fetchone()[0])
        assignment_digest = _candidate_assignment_digest(candidate)
        metadata = {
            "schema": CANDIDATE_SCHEMA,
            "classifier_version": CLASSIFIER_VERSION,
            "eligible_event_count": str(eligible_events),
            "eligible_clause_count": str(eligible_clauses),
            "assignment_count": str(assignment_count),
            "assignment_digest": assignment_digest,
        }
        candidate.executemany(
            "INSERT INTO artifact_meta(key,value) VALUES(?,?)",
            sorted(metadata.items()),
        )
        candidate.commit()
        check = candidate.execute("PRAGMA quick_check").fetchone()
        if not check or str(check[0]) != "ok":
            raise RuntimeError("candidate_sqlite_quick_check_failed")
        return {
            "eligible_event_count": eligible_events,
            "eligible_clause_count": eligible_clauses,
            "assignment_count": assignment_count,
            "assignment_digest": assignment_digest,
        }
    finally:
        candidate.close()


def prepare_reclassification(store: ProjectMemoryStore, *,
                             target_config_path: Path,
                             prior_config_path: Path | None = None,
                             prior_without_projects: tuple[str, ...] = (),
                             output_dir: Path | None = None,
                             progress: Progress = None) -> Path:
    """Prepare a private signed candidate without changing live assignments."""
    target_config_path = target_config_path.expanduser().resolve()
    target_bytes = target_config_path.read_bytes()
    target_config = load_config(target_config_path)
    removals = tuple(sorted(set(str(item) for item in prior_without_projects)))
    if prior_config_path is not None and removals:
        raise RuntimeError(
            "choose_prior_config_or_prior_project_removal_not_both")
    if removals:
        available = {str(row["id"]) for row in target_config.get("projects") or []}
        missing = sorted(set(removals).difference(available))
        if missing:
            raise RuntimeError(
                "prior_project_removal_missing:" + ",".join(missing))
        prior_config = dict(target_config)
        prior_config["projects"] = [
            row for row in target_config.get("projects") or []
            if str(row["id"]) not in set(removals)
        ]
        prior_bytes = _canonical(prior_config)
        prior_derivation = {"removed_projects": list(removals)}
    else:
        prior_config_path = (
            prior_config_path.expanduser().resolve()
            if prior_config_path is not None else target_config_path
        )
        prior_bytes = prior_config_path.read_bytes()
        prior_config = load_config(prior_config_path)
        prior_derivation = {"config_file": prior_config_path.name}
    target_digest = classification_digest(target_config)
    prior_version = _stored_classifier_version(store)
    expected_prior_digest = classification_digest_for_version(
        prior_config, prior_version
    )
    stored_row = store.db.execute(
        "SELECT value FROM schema_meta WHERE key='classification_config_digest'"
    ).fetchone()
    stored_digest = str(stored_row[0]) if stored_row else ""
    if stored_digest and not hmac.compare_digest(stored_digest, expected_prior_digest):
        raise RuntimeError("prior_config_does_not_match_live_classification")
    _report(
        progress,
        "policy validated prior=%s target=%s" %
        (stored_digest[:12] or "unbound", target_digest[:12]),
    )
    assignments_before = assignment_table_digest(store)
    source_reconciliation = _reconcile_sources(
        store, target_config, prior_config, progress
    )
    _report(
        progress,
        "source reconciliation complete inserted=%d existing=%d" %
        (source_reconciliation["inserted"],
         source_reconciliation["existing"]),
    )
    if assignment_table_digest(store) != assignments_before:
        raise RuntimeError("prepare_mutated_live_assignments")
    current_stored = store.db.execute(
        "SELECT value FROM schema_meta WHERE key='classification_config_digest'"
    ).fetchone()
    if (str(current_stored[0]) if current_stored else "") != stored_digest:
        raise RuntimeError("prepare_mutated_live_classification_digest")
    input_digest = classification_input_digest(store)
    quarantine_count = int(store.db.execute(
        "SELECT COUNT(*) FROM prompt_events p WHERE " +
        NONAUTHORITATIVE_OWNER_SQL
    ).fetchone()[0])
    prepared_at = utc_now()
    directory = _inside_private_root(
        store, output_dir or (store.root / "classification-rebuilds")
    )
    directory.mkdir(parents=True, exist_ok=True)
    nonce = "%d-%d" % (time.time_ns(), os.getpid())
    candidate_name = "candidate-%s.sqlite3" % nonce
    candidate_final = directory / candidate_name
    candidate_temp = directory / (candidate_name + ".tmp")
    envelope_path = directory / ("candidate-%s.json" % nonce)
    try:
        _report(progress, "building private candidate")
        candidate_stats = _create_candidate(
            store, target_config, candidate_temp, prepared_at, progress
        )
        _report(
            progress,
            "candidate classified events=%d clauses=%d assignments=%d" %
            (candidate_stats["eligible_event_count"],
             candidate_stats["eligible_clause_count"],
             candidate_stats["assignment_count"]),
        )
        if classification_input_digest(store) != input_digest:
            raise RuntimeError("classification_inputs_changed_during_prepare")
        if assignment_table_digest(store) != assignments_before:
            raise RuntimeError("live_assignments_changed_during_prepare")
        current_stored = store.db.execute(
            "SELECT value FROM schema_meta WHERE key='classification_config_digest'"
        ).fetchone()
        if (str(current_stored[0]) if current_stored else "") != stored_digest:
            raise RuntimeError("classification_digest_changed_during_prepare")
        os.replace(candidate_temp, candidate_final)
        candidate_sha = _sha256_file(candidate_final)
        rebuild_id = "rebuild:" + store.keyed(
            "classification-rebuild",
            "%s:%s:%s" % (target_digest, input_digest, candidate_sha),
        )[:32]
        body = {
            "schema": ARTIFACT_SCHEMA,
            "rebuild_id": rebuild_id,
            "prepared_at": prepared_at,
            "candidate_filename": candidate_final.name,
            "candidate_sha256": candidate_sha,
            "candidate_assignment_digest": candidate_stats["assignment_digest"],
            "candidate_assignment_count": candidate_stats["assignment_count"],
            "eligible_event_count": candidate_stats["eligible_event_count"],
            "eligible_clause_count": candidate_stats["eligible_clause_count"],
            "quarantined_event_count": quarantine_count,
            "classification_input_digest": input_digest,
            "live_assignment_digest": assignments_before,
            "prior_config_digest": stored_digest,
            "prior_classifier_version": prior_version,
            "target_config_digest": target_digest,
            "target_config_sha256": hashlib.sha256(target_bytes).hexdigest(),
            "prior_config_sha256": hashlib.sha256(prior_bytes).hexdigest(),
            "prior_config_derivation": prior_derivation,
            "classifier_version": CLASSIFIER_VERSION,
            "source_reconciliation": source_reconciliation,
        }
        signature = hmac.new(store.key, _canonical(body), hashlib.sha256).hexdigest()
        envelope = {"body": body, "signature": signature}
        _atomic_bytes(envelope_path, _canonical(envelope) + b"\n")
        _report(
            progress,
            "signed candidate ready rebuild=%s" % rebuild_id,
        )
        return envelope_path
    except Exception:
        for path in (candidate_temp, candidate_final, envelope_path):
            try:
                path.unlink()
            except OSError:
                pass
        raise


def _load_envelope(store: ProjectMemoryStore, artifact_path: Path) -> tuple[dict, str, Path]:
    artifact_path = _inside_private_root(store, artifact_path)
    envelope = json.loads(artifact_path.read_text(encoding="utf-8"))
    if not isinstance(envelope, dict) or not isinstance(envelope.get("body"), dict):
        raise RuntimeError("classification_artifact_invalid")
    body = envelope["body"]
    signature = str(envelope.get("signature") or "")
    expected = hmac.new(store.key, _canonical(body), hashlib.sha256).hexdigest()
    if (body.get("schema") != ARTIFACT_SCHEMA or
            not hmac.compare_digest(signature, expected)):
        raise RuntimeError("classification_artifact_signature_invalid")
    filename = str(body.get("candidate_filename") or "")
    if not filename or Path(filename).name != filename:
        raise RuntimeError("classification_candidate_filename_invalid")
    candidate_path = _inside_private_root(store, artifact_path.parent / filename)
    if _sha256_file(candidate_path) != str(body.get("candidate_sha256")):
        raise RuntimeError("classification_candidate_sha256_mismatch")
    return body, signature, candidate_path


def _open_candidate(candidate_path: Path) -> sqlite3.Connection:
    candidate = sqlite3.connect(candidate_path.as_uri() + "?mode=ro&immutable=1",
                                uri=True)
    candidate.row_factory = sqlite3.Row
    check = candidate.execute("PRAGMA quick_check").fetchone()
    if not check or str(check[0]) != "ok":
        candidate.close()
        raise RuntimeError("classification_candidate_quick_check_failed")
    unsafe = int(candidate.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('trigger','view')"
    ).fetchone()[0])
    if unsafe:
        candidate.close()
        raise RuntimeError("classification_candidate_unsafe_schema")
    metadata = dict(candidate.execute(
        "SELECT key,value FROM artifact_meta"
    ).fetchall())
    if metadata.get("schema") != CANDIDATE_SCHEMA:
        candidate.close()
        raise RuntimeError("classification_candidate_schema_invalid")
    return candidate


def _mark_cache_pending(store: ProjectMemoryStore, rebuild_id: str) -> None:
    path = cache_path(store.root)
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        body = envelope["body"]
        signature = str(envelope["signature"])
        expected = hmac.new(store.key, _canonical(body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return
        body["built_at"] = "1970-01-01T00:00:00+00:00"
        body["migration_pending"] = rebuild_id
        refreshed = {
            "body": body,
            "signature": hmac.new(
                store.key, _canonical(body), hashlib.sha256
            ).hexdigest(),
        }
        _atomic_bytes(path, _canonical(refreshed) + b"\n")
    except Exception:
        return


def _refresh_applied_cache(store: ProjectMemoryStore, config: dict,
                           rebuild_id: str,
                           progress: Progress = None) -> dict:
    _report(progress, "refreshing signed hot cache")
    built_path = build_contract_cache(store, config)
    cache_sha256 = _sha256_file(built_path)
    refreshed_at = utc_now()
    with store.immediate():
        store.db.execute(
            "UPDATE classification_rebuilds "
            "SET cache_refreshed_at=?,cache_sha256=? "
            "WHERE rebuild_id=?",
            (refreshed_at, cache_sha256, rebuild_id),
        )
    _report(progress, "signed hot cache refreshed")
    return dict(store.db.execute(
        "SELECT * FROM classification_rebuilds WHERE rebuild_id=?",
        (rebuild_id,),
    ).fetchone())


def _assert_applied_state(store: ProjectMemoryStore, receipt,
                          target_config: dict, target_digest: str,
                          rebuild_id: str) -> None:
    classification = dict(store.db.execute(
        "SELECT key,value FROM schema_meta WHERE key IN "
        "('classification_config_digest','classification_classifier_version',"
        "'classification_last_rebuild_id')"
    ).fetchall())
    expected_classification = {
        "classification_config_digest": target_digest,
        "classification_classifier_version": CLASSIFIER_VERSION,
        "classification_last_rebuild_id": rebuild_id,
    }
    if classification != expected_classification:
        raise RuntimeError("classification_applied_metadata_drift")
    if (_applied_candidate_assignment_digest(store) !=
            str(receipt["assignment_digest"])):
        raise RuntimeError("classification_applied_assignment_drift")
    active_projects = set(store.active_project_ids())
    expected_projects = {
        str(row["id"]) for row in target_config.get("projects") or []
        if bool(row.get("active", True))
    } | {UNKNOWN_PROJECT}
    if active_projects != expected_projects:
        raise RuntimeError("classification_applied_project_state_drift")


def _applied_cache_is_current(store: ProjectMemoryStore, receipt,
                              target_digest: str, rebuild_id: str) -> bool:
    expected_sha = str(receipt["cache_sha256"] or "")
    if not expected_sha:
        return False
    path = cache_path(store.root)
    if not path.exists() or not hmac.compare_digest(
            _sha256_file(path), expected_sha):
        return False
    body = load_contract_cache(store.root, store.key)
    if body is None or body.get("migration_pending") is not None:
        return False
    try:
        built_at = datetime.fromisoformat(
            str(body["built_at"]).replace("Z", "+00:00")
        )
        if built_at.tzinfo is None:
            return False
        age_seconds = max(
            0.0,
            (datetime.now(timezone.utc) -
             built_at.astimezone(timezone.utc)).total_seconds(),
        )
        max_age = float(
            (body.get("policy") or {}).get("max_cache_age_seconds", 7500)
        )
        if age_seconds > max_age:
            return False
    except (KeyError, TypeError, ValueError):
        return False
    expected_classification = {
        "classification_config_digest": target_digest,
        "classification_classifier_version": CLASSIFIER_VERSION,
        "classification_last_rebuild_id": rebuild_id,
    }
    return (
        body.get("classification") == expected_classification and
        body.get("manifest") == store.manifest_digest()
    )


def apply_reclassification(store: ProjectMemoryStore, *, artifact_path: Path,
                           target_config_path: Path,
                           progress: Progress = None) -> dict:
    """Atomically replace assignments after rechecking signed candidate inputs."""
    _report(progress, "verifying signed candidate envelope")
    target_config_path = target_config_path.expanduser().resolve()
    target_bytes = target_config_path.read_bytes()
    target_config_sha = hashlib.sha256(target_bytes).hexdigest()
    target_config = load_config(target_config_path)
    target_digest = classification_digest(target_config)
    body, artifact_hmac, candidate_path = _load_envelope(store, artifact_path)
    rebuild_id = str(body["rebuild_id"])
    if (body.get("classifier_version") != CLASSIFIER_VERSION or
            body.get("target_config_digest") != target_digest or
            body.get("target_config_sha256") != target_config_sha):
        raise RuntimeError("classification_target_config_drift")
    existing_receipt = store.db.execute(
        "SELECT * FROM classification_rebuilds WHERE rebuild_id=?", (rebuild_id,)
    ).fetchone()
    if existing_receipt:
        if (str(existing_receipt["artifact_hmac"]) != artifact_hmac or
                str(existing_receipt["target_config_digest"]) != target_digest):
            raise RuntimeError("classification_rebuild_receipt_collision")
        store.db.execute("BEGIN")
        try:
            current_receipt = store.db.execute(
                "SELECT * FROM classification_rebuilds WHERE rebuild_id=?",
                (rebuild_id,),
            ).fetchone()
            if current_receipt is None:
                raise RuntimeError(
                    "classification_rebuild_receipt_disappeared")
            _assert_applied_state(
                store, current_receipt, target_config, target_digest,
                rebuild_id,
            )
            cache_is_current = _applied_cache_is_current(
                store, current_receipt, target_digest, rebuild_id
            )
            store.db.commit()
        except Exception:
            store.db.rollback()
            raise
        if cache_is_current:
            _report(progress, "existing receipt and signed cache verified")
            return dict(current_receipt)
        return _refresh_applied_cache(
            store, target_config, rebuild_id, progress
        )
    stored_row = store.db.execute(
        "SELECT value FROM schema_meta WHERE key='classification_config_digest'"
    ).fetchone()
    stored_digest = str(stored_row[0]) if stored_row else ""
    if stored_digest != str(body.get("prior_config_digest") or ""):
        raise RuntimeError("classification_live_digest_drift")
    if classification_input_digest(store) != str(body["classification_input_digest"]):
        raise RuntimeError("classification_live_input_drift")
    if assignment_table_digest(store) != str(body["live_assignment_digest"]):
        raise RuntimeError("classification_live_assignment_drift")
    candidate = _open_candidate(candidate_path)
    _report(progress, "validating candidate coverage and project scope")
    try:
        metadata = dict(candidate.execute(
            "SELECT key,value FROM artifact_meta"
        ).fetchall())
        if (metadata.get("classifier_version") != CLASSIFIER_VERSION or
                metadata.get("assignment_digest") !=
                str(body["candidate_assignment_digest"]) or
                int(metadata.get("assignment_count", -1)) !=
                int(body["candidate_assignment_count"]) or
                _candidate_assignment_digest(candidate) !=
                str(body["candidate_assignment_digest"])):
            raise RuntimeError("classification_candidate_metadata_mismatch")
        project_ids = {
            str(row[0]) for row in candidate.execute(
                "SELECT DISTINCT project_id FROM candidate_assignments"
            )
        }
        allowed_projects = {
            str(row["id"]) for row in target_config.get("projects") or []
        } | {UNKNOWN_PROJECT}
        if not project_ids.issubset(allowed_projects):
            raise RuntimeError("classification_candidate_unknown_project")
    finally:
        candidate.close()
    _mark_cache_pending(store, rebuild_id)
    _report(progress, "applying atomic assignment replacement")
    store.db.execute("ATTACH DATABASE ? AS candidate", (str(candidate_path),))
    applied_at = utc_now()
    try:
        with store.immediate():
            stored_row = store.db.execute(
                "SELECT value FROM schema_meta "
                "WHERE key='classification_config_digest'"
            ).fetchone()
            if (str(stored_row[0]) if stored_row else "") != stored_digest:
                raise RuntimeError("classification_live_digest_drift")
            if (classification_input_digest(store) !=
                    str(body["classification_input_digest"])):
                raise RuntimeError("classification_live_input_drift")
            if assignment_table_digest(store) != str(body["live_assignment_digest"]):
                raise RuntimeError("classification_live_assignment_drift")
            if _sha256_file(candidate_path) != str(body["candidate_sha256"]):
                raise RuntimeError("classification_candidate_changed_during_apply")
            if hashlib.sha256(target_config_path.read_bytes()).hexdigest() != target_config_sha:
                raise RuntimeError("classification_target_config_changed_during_apply")
            candidate_count = int(store.db.execute(
                "SELECT COUNT(*) FROM candidate.candidate_assignments"
            ).fetchone()[0])
            candidate_clauses = int(store.db.execute(
                "SELECT COUNT(DISTINCT clause_id) "
                "FROM candidate.candidate_assignments"
            ).fetchone()[0])
            eligible_events = int(store.db.execute(
                "SELECT COUNT(*) FROM prompt_events p WHERE " +
                AUTHORITATIVE_EVENT_SQL
            ).fetchone()[0])
            eligible_clauses = int(store.db.execute(
                "SELECT COUNT(*) FROM clauses c JOIN prompt_events p "
                "ON p.event_id=c.event_id WHERE " +
                AUTHORITATIVE_EVENT_SQL
            ).fetchone()[0])
            if (eligible_events != int(body["eligible_event_count"]) or
                    eligible_clauses != int(body["eligible_clause_count"]) or
                    candidate_count != int(body["candidate_assignment_count"]) or
                    candidate_clauses != eligible_clauses):
                raise RuntimeError("classification_candidate_coverage_mismatch")
            missing = int(store.db.execute(
                "SELECT COUNT(*) FROM clauses c JOIN prompt_events p "
                "ON p.event_id=c.event_id WHERE " + AUTHORITATIVE_EVENT_SQL +
                " AND NOT EXISTS (SELECT 1 FROM candidate.candidate_assignments ca "
                "WHERE ca.clause_id=c.clause_id)"
            ).fetchone()[0])
            extra = int(store.db.execute(
                "SELECT COUNT(*) FROM candidate.candidate_assignments ca "
                "WHERE NOT EXISTS (SELECT 1 FROM clauses c JOIN prompt_events p "
                "ON p.event_id=c.event_id WHERE c.clause_id=ca.clause_id AND " +
                AUTHORITATIVE_EVENT_SQL + ")"
            ).fetchone()[0])
            if missing or extra:
                raise RuntimeError("classification_candidate_clause_set_mismatch")
            store.db.execute("UPDATE projects SET active=0,pinned=0")
            store.ensure_projects(target_config)
            store.db.execute("DELETE FROM clause_assignments")
            store.db.execute(
                "INSERT INTO clause_assignments(revision_id,clause_id,project_id,"
                "score,confidence_band,evidence_codes_json,classifier_version,created_at) "
                "SELECT revision_id,clause_id,project_id,score,confidence_band,"
                "evidence_codes_json,classifier_version,created_at "
                "FROM candidate.candidate_assignments"
            )
            quarantined = store.db.execute(
                "UPDATE prompt_events AS p SET provenance='unknown' WHERE " +
                NONAUTHORITATIVE_OWNER_SQL
            ).rowcount
            if int(quarantined) != int(body["quarantined_event_count"]):
                raise RuntimeError("classification_quarantine_count_mismatch")
            store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_config_digest',?)", (target_digest,)
            )
            store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_classifier_version',?)",
                (CLASSIFIER_VERSION,),
            )
            store.db.execute(
                "INSERT OR REPLACE INTO schema_meta(key,value) "
                "VALUES('classification_last_rebuild_id',?)", (rebuild_id,)
            )
            store.db.execute(
                "UPDATE project_review_state SET attempt_generation="
                "CASE WHEN attempt_generation<=published_generation "
                "THEN published_generation+1 ELSE attempt_generation END,"
                "attempt_started_at=?,last_error_code="
                "'classification_rebuild_requires_republication' "
                "WHERE project_id IN (SELECT project_id FROM projects WHERE active=1)",
                (applied_at,),
            )
            store.db.execute(
                "INSERT INTO classification_rebuilds(rebuild_id,artifact_hmac,"
                "candidate_sha256,prior_config_digest,target_config_digest,input_digest,"
                "assignment_digest,classifier_version,eligible_event_count,"
                "eligible_clause_count,quarantined_event_count,applied_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (rebuild_id, artifact_hmac, body["candidate_sha256"],
                 stored_digest, target_digest, body["classification_input_digest"],
                 body["candidate_assignment_digest"], CLASSIFIER_VERSION,
                 body["eligible_event_count"], body["eligible_clause_count"],
                 quarantined, applied_at),
            )
            if (_sha256_file(candidate_path) != str(body["candidate_sha256"]) or
                    hashlib.sha256(target_config_path.read_bytes()).hexdigest() !=
                    target_config_sha):
                raise RuntimeError("classification_external_input_changed_before_commit")
    finally:
        store.db.execute("DETACH DATABASE candidate")
    _report(progress, "atomic assignment replacement committed")
    return _refresh_applied_cache(
        store, target_config, rebuild_id, progress
    )
