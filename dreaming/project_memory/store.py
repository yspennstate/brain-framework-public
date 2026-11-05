"""SQLite store for the deployment-local project memory sidecar."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import SCHEMA_VERSION
from .clauses import project_prompt
from .classify import (CLASSIFIER_VERSION, UNKNOWN_PROJECT, classification_digest,
                       classify_clause)

HERE = Path(__file__).resolve().parent
AUTHORITATIVE_EVENT_SQL = (
    "p.provenance='owner' AND p.source_id<>'hook' "
    "AND p.source_content_hmac IS NOT NULL "
    "AND p.source_content_hmac=p.content_hmac AND p.projection_version>=5"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_root() -> Path:
    configured = os.environ.get("PROJECT_MEMORY_STATE")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".brain-framework" / "state" / "project_memory").resolve()


def config_path() -> Path:
    configured = os.environ.get("PROJECT_MEMORY_CONFIG")
    return Path(configured).expanduser().resolve() if configured else state_root() / "projects.json"


def database_path() -> Path:
    configured = os.environ.get("PROJECT_MEMORY_DB")
    return Path(configured).expanduser().resolve() if configured else state_root() / "project_memory.sqlite3"


def prompt_ledger_path() -> Path:
    configured = os.environ.get("PROJECT_MEMORY_PROMPTS")
    if configured:
        return Path(configured).expanduser().resolve()
    return (state_root() / "owner-prompts.jsonl").resolve()


def _ensure_private_dir(path: Path) -> None:
    resolved = path.resolve()
    if any((parent / ".git").exists() for parent in (resolved, *resolved.parents)):
        raise RuntimeError("project-memory private state must not be inside a Git worktree")
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        marker = path / ".private-acl-v1"
        if not marker.exists():
            user = os.environ.get("USERNAME")
            if not user:
                raise RuntimeError("cannot determine current Windows identity for private state ACL")
            creation_no_window = (0x08000000 if os.name == "nt" else 0)
            result = subprocess.run(
                ["icacls.exe", str(path), "/inheritance:r", "/grant:r",
                 "%s:(OI)(CI)F" % user, "SYSTEM:(OI)(CI)F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=creation_no_window, timeout=10, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError("failed to establish private Windows ACL")
            marker.write_text("private ACL established\n", encoding="ascii")
        return
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


_PRIO_DARWIN_PROCESS = 4
_PRIO_DARWIN_BG = 0x1000


def _darwin_background() -> int:
    if sys.platform != "darwin":
        return 0
    try:
        return os.getpriority(_PRIO_DARWIN_PROCESS, 0)
    except OSError:
        return 0


def _set_darwin_background(value: int) -> None:
    try:
        os.setpriority(_PRIO_DARWIN_PROCESS, 0, value)
    except OSError:
        pass


class ProjectMemoryStore:
    def __init__(self, path: Path | None = None, *, root: Path | None = None,
                 busy_timeout_ms: int = 500, wal_autocheckpoint: int | None = None) -> None:
        self.root = (root or state_root()).resolve()
        _ensure_private_dir(self.root)
        self.path = (path or database_path()).resolve()
        self.key = self._load_key()
        self.db = sqlite3.connect(str(self.path), timeout=max(0.05, busy_timeout_ms / 1000.0))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=%d" % int(busy_timeout_ms))
        if wal_autocheckpoint is not None:
            self.db.execute("PRAGMA wal_autocheckpoint=%d" % int(wal_autocheckpoint))
        self._install_schema()

    def _load_key(self) -> bytes:
        path = self.root / "project_memory.key"
        if not path.exists():
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            try:
                fd = os.open(str(path), flags, 0o600)
                try:
                    os.write(fd, secrets.token_bytes(32))
                    os.fsync(fd)
                finally:
                    os.close(fd)
            except FileExistsError:
                pass
        key = path.read_bytes()
        if len(key) < 32:
            raise RuntimeError("project-memory HMAC key is missing or truncated")
        return key[:32]

    def _install_schema(self) -> None:
        self.db.executescript((HERE / "schema.sql").read_text(encoding="utf-8"))
        with self.db:
            columns = {str(row[1]) for row in self.db.execute("PRAGMA table_info(prompt_events)")}
            if "projection_version" not in columns:
                self.db.execute(
                    "ALTER TABLE prompt_events ADD COLUMN projection_version INTEGER NOT NULL DEFAULT 2"
                )
            if "source_content_hmac" not in columns:
                self.db.execute("ALTER TABLE prompt_events ADD COLUMN source_content_hmac TEXT")
            rebuild_columns = {
                str(row[1]) for row in
                self.db.execute("PRAGMA table_info(classification_rebuilds)")
            }
            if "cache_sha256" not in rebuild_columns:
                self.db.execute(
                    "ALTER TABLE classification_rebuilds ADD COLUMN cache_sha256 TEXT"
                )
            row = self.db.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            prior = int(row[0]) if row else SCHEMA_VERSION
            if prior not in {2, 3, 4, SCHEMA_VERSION}:
                raise RuntimeError("project-memory schema version mismatch")
            # v3 makes base-span semantics explicit.  Every span is represented
            # by one or more semantic clauses OR exactly one inert disposition.
            # Backfill is deterministic and stores no prompt bytes.
            if prior == 2:
                self.db.execute(
                    "INSERT OR IGNORE INTO coverage_dispositions(span_id,event_id,disposition,reason_code,created_at) "
                    "SELECT s.span_id,s.event_id,CASE WHEN s.span_kind='separator' "
                    "THEN 'non_actionable_separator' WHEN s.span_kind='unparsed_overflow' "
                    "THEN 'retained_overflow' ELSE 'retained_unparsed' END,"
                    "'no_semantic_clause',? FROM coverage_spans s WHERE NOT EXISTS "
                    "(SELECT 1 FROM clauses c WHERE c.span_id=s.span_id)",
                    (utc_now(),),
                )
            # v4 records whether an event has immutable authoritative source
            # bytes. Hook observations deliberately retain no raw bytes until
            # a transcript/rollout row reconciles them.
            if prior in {2, 3}:
                self.db.execute(
                    "UPDATE prompt_events SET source_content_hmac=content_hmac "
                    "WHERE source_id<>'hook' AND source_content_hmac IS NULL"
                )
            # v5 replaces the original raw-wrapper adapters with a shared
            # human-only projection and explicit owner-facing source filter.
            # Preserve v1 rows as immutable evidence, but make them inert so
            # harness, fork, and automation text cannot remain owner policy.
            if prior in {2, 3, 4}:
                self.db.execute(
                    "UPDATE prompt_events SET provenance='automation' WHERE "
                    "source_id LIKE 'claude-transcript:%' OR "
                    "source_id LIKE 'codex-rollout:%'"
                )
            invalid_source_binding = self.db.execute(
                "SELECT COUNT(*) FROM prompt_events WHERE "
                "(source_id='hook' AND source_content_hmac IS NOT NULL) OR "
                "(source_id<>'hook' AND (source_content_hmac IS NULL OR source_content_hmac<>content_hmac))"
            ).fetchone()[0]
            if int(invalid_source_binding):
                raise RuntimeError("project-memory source binding mismatch")
            self.db.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                            (str(SCHEMA_VERSION),))

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "ProjectMemoryStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def keyed(self, domain: str, value: object) -> str:
        material = (domain + "\x1f" + str(value if value is not None else "")).encode("utf-8", "replace")
        return hmac.new(self.key, material, hashlib.sha256).hexdigest()

    def session_hmac(self, session: str) -> str:
        return self.keyed("session", session or "unknown")[:32]

    @contextmanager
    def immediate(self):
        # Every prompt hook waits on this lock. A darwin Background runner (launchd ProcessType) holding it at load 25
        # ran 0.3 s of work in 4-131 s and failed hooks closed; leave Background while the lock is held.
        background = _darwin_background()
        if background:
            _set_darwin_background(0)
        try:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except Exception:
                self.db.rollback()
                raise
            else:
                self.db.commit()
        finally:
            if background:
                _set_darwin_background(_PRIO_DARWIN_BG)

    def ensure_projects(self, config: dict) -> None:
        now = utc_now()
        rows = list(config.get("projects") or []) + [{"id": UNKNOWN_PROJECT, "active": True, "pinned": False}]
        for row in rows:
            self.db.execute(
                "INSERT INTO projects(project_id,active,pinned,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET active=excluded.active,pinned=excluded.pinned",
                (row["id"], int(bool(row.get("active", True))), int(bool(row.get("pinned", False))), now),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO project_review_state(project_id) VALUES(?)", (row["id"],)
            )

    def _session_prior(self, *, event_id: str, session_hmac: str, occurred_at: str,
                       source_id: str, source_seq: int) -> tuple[str, ...]:
        preceding = self.db.execute(
            "SELECT p.event_id FROM prompt_events p WHERE p.session_hmac=? AND " +
            AUTHORITATIVE_EVENT_SQL + " AND p.event_id<>? AND ("
            "p.occurred_at<? OR (p.occurred_at=? AND p.source_id<?) OR "
            "(p.occurred_at=? AND p.source_id=? AND p.source_seq<?) OR "
            "(p.occurred_at=? AND p.source_id=? AND p.source_seq=? AND p.event_id<?)) "
            "ORDER BY p.occurred_at DESC,p.source_id DESC,p.source_seq DESC,p.event_id DESC LIMIT 4",
            (session_hmac, event_id, occurred_at, occurred_at, source_id,
             occurred_at, source_id, int(source_seq), occurred_at, source_id,
             int(source_seq), event_id),
        ).fetchall()
        if not preceding:
            return ()
        event_ids = [str(row[0]) for row in preceding]
        marks = ",".join("?" for _ in event_ids)
        return tuple(str(row[0]) for row in self.db.execute(
            "SELECT DISTINCT ca.project_id FROM clause_assignments ca "
            "JOIN clauses c ON c.clause_id=ca.clause_id "
            "WHERE ca.classifier_version=? AND c.event_id IN (" + marks + ") "
            "AND ca.project_id<>? ORDER BY ca.project_id",
            (CLASSIFIER_VERSION, *event_ids, UNKNOWN_PROJECT),
        ))

    def _later_authoritative_event_exists(
            self, *, event_id: str, session_hmac: str, occurred_at: str,
            source_id: str, source_seq: int) -> bool:
        return self.db.execute(
            "SELECT 1 FROM prompt_events p WHERE p.session_hmac=? AND " +
            AUTHORITATIVE_EVENT_SQL + " AND p.event_id<>? AND ("
            "p.occurred_at>? OR (p.occurred_at=? AND p.source_id>?) OR "
            "(p.occurred_at=? AND p.source_id=? AND p.source_seq>?) OR "
            "(p.occurred_at=? AND p.source_id=? AND p.source_seq=? AND p.event_id>?)) "
            "LIMIT 1",
            (session_hmac, event_id, occurred_at, occurred_at, source_id,
             occurred_at, source_id, int(source_seq), occurred_at, source_id,
             int(source_seq), event_id),
        ).fetchone() is not None

    def _insert_assignments(self, *, event_id: str, clauses: list[dict], cwd: str,
                            source_project: str, session_hmac: str, occurred_at: str,
                            source_id: str, source_seq: int, observed: str,
                            config: dict) -> None:
        session_prior = self._session_prior(
            event_id=event_id, session_hmac=session_hmac, occurred_at=occurred_at,
            source_id=source_id, source_seq=source_seq,
        )
        for clause in clauses:
            assignments = classify_clause(
                clause["raw"], cwd=cwd, source_project=source_project,
                session_prior=session_prior, config=config,
            )
            for assignment in assignments:
                revision = "assign:" + self.keyed(
                    "assignment", "%s:%s:%s" %
                    (clause["clause_id"], assignment["project_id"], CLASSIFIER_VERSION)
                )[:32]
                self.db.execute(
                    "INSERT OR IGNORE INTO clause_assignments(revision_id,clause_id,project_id,score,"
                    "confidence_band,evidence_codes_json,classifier_version,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (revision, clause["clause_id"], assignment["project_id"], assignment["score"],
                     assignment["confidence_band"], json.dumps(assignment["evidence_codes"]),
                     CLASSIFIER_VERSION, observed),
                )

    def _insert_projected_event(
            self, *, event_id: str, text: str, source_id: str, source_seq: int,
            source_ref_hmac: str, content_hmac: str, occurred_at: str,
            observed: str, session_hmac: str, provenance: str,
            runtime: str) -> list[dict]:
        def scoped_keyed(domain: str, value: str) -> str:
            if domain in {"span", "span-bytes", "clause"}:
                value = event_id + "\x1f" + value
            return self.keyed(domain, value)

        spans, clauses, overflow = project_prompt(text, scoped_keyed)
        self.db.execute(
            "INSERT INTO prompt_events(event_id,source_id,source_seq,source_ref_hmac,content_hmac,"
            "source_content_hmac,occurred_at,observed_at,session_hmac,provenance,runtime,"
            "span_count,clause_count,parser_overflow,projection_version) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, source_id, int(source_seq), source_ref_hmac, content_hmac,
             content_hmac if source_id != "hook" else None,
             occurred_at or observed, observed, session_hmac, provenance,
             runtime or "unknown", len(spans), len(clauses), int(overflow),
             SCHEMA_VERSION),
        )
        for span in spans:
            self.db.execute(
                "INSERT INTO coverage_spans(span_id,event_id,ordinal,start_offset,end_offset,"
                "span_kind,span_hmac) VALUES(?,?,?,?,?,?,?)",
                (span["span_id"], event_id, span["ordinal"], span["start"],
                 span["end"], span["kind"], span["span_hmac"]),
            )
        for clause in clauses:
            self.db.execute(
                "INSERT INTO clauses(clause_id,event_id,span_id,ordinal,role,weight,"
                "features_json,exact_categories_json,forbidden_ops_json,actionable) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (clause["clause_id"], event_id, clause["span_id"],
                 clause["ordinal"], clause["role"], clause["weight"],
                 json.dumps(clause["features"], separators=(",", ":")),
                 json.dumps(clause["exact_categories"], separators=(",", ":")),
                 json.dumps(clause["forbidden_ops"], separators=(",", ":")),
                 int(clause["actionable"])),
            )
            state = "unseen" if clause["actionable"] else "non_actionable"
            self.db.execute(
                "INSERT INTO obligation_progress(clause_id,state,updated_at) "
                "VALUES(?,?,?)",
                (clause["clause_id"], state, observed),
            )
        clause_spans = {str(clause["span_id"]) for clause in clauses}
        for span in spans:
            if str(span["span_id"]) in clause_spans:
                continue
            disposition = (
                "non_actionable_separator" if span["kind"] == "separator"
                else "retained_overflow" if span["kind"] == "unparsed_overflow"
                else "retained_unparsed"
            )
            self.db.execute(
                "INSERT INTO coverage_dispositions(span_id,event_id,disposition,"
                "reason_code,created_at) VALUES(?,?,?,?,?)",
                (span["span_id"], event_id, disposition,
                 "no_semantic_clause", observed),
            )
        return clauses

    def _reconcile_existing_authority(
            self, *, existing: sqlite3.Row, text: str, source_id: str,
            source_seq: int, source_ref_hmac: str, content_hmac: str,
            session_hmac: str, occurred_at: str, observed: str,
            runtime: str) -> str:
        """Stage a current source-bound copy without changing live assignments."""
        old_event_id = str(existing["event_id"])
        quarantine_source_id = (
            "authority-quarantine:" +
            self.keyed("authority-quarantine-source", old_event_id)[:16]
        )
        quarantine_source_seq = int(existing["ingest_seq"])
        quarantine_ref = self.keyed(
            "source-ref",
            "%s:%s" % (quarantine_source_id, quarantine_source_seq),
        )
        reconciled_event_id = "prompt:" + self.keyed(
            "prompt-event-reconciled",
            "%s:%s:%s:%s:%s" %
            (source_id, source_seq, session_hmac, content_hmac, SCHEMA_VERSION),
        )[:32]
        self.db.execute(
            "UPDATE prompt_events SET source_id=?,source_seq=?,source_ref_hmac=?,"
            "source_content_hmac=content_hmac,provenance='owner',projection_version=0 "
            "WHERE event_id=?",
            (quarantine_source_id, quarantine_source_seq, quarantine_ref,
             old_event_id),
        )
        self._insert_projected_event(
            event_id=reconciled_event_id, text=text, source_id=source_id,
            source_seq=source_seq, source_ref_hmac=source_ref_hmac,
            content_hmac=content_hmac, occurred_at=occurred_at,
            observed=observed, session_hmac=session_hmac,
            provenance="owner", runtime=runtime,
        )
        return reconciled_event_id

    def ingest_prompt(self, text: str, *, source_id: str, source_seq: int, session: str,
                      occurred_at: str, provenance: str, runtime: str, cwd: str,
                      source_project: str, config: dict,
                      classify_assignments: bool = True,
                      reconcile_existing_authority: bool = False) -> tuple[str, bool]:
        """Ingest one occurrence without persisting prompt text.

        Hook observations are reconciled to their later authoritative ledger row by
        keyed content and session identity.  Repeated identical occurrences remain
        distinct because each hook row is consumed at most once.
        """
        observed = utc_now()
        content_hmac = self.keyed("prompt-content", text)
        sess_hmac = self.session_hmac(session)
        source_ref_hmac = self.keyed("source-ref", "%s:%s" % (source_id, source_seq))
        normalized_provenance = (
            provenance if provenance in {"owner", "automation", "system", "unknown"} else "unknown"
        )
        if source_id == "hook":
            normalized_provenance = "unknown"
        authoritative = normalized_provenance == "owner" and source_id != "hook"
        if reconcile_existing_authority and classify_assignments:
            raise RuntimeError(
                "authority_reconciliation_requires_deferred_classification")
        with self.immediate():
            if authoritative and classify_assignments:
                stored_digest = self.db.execute(
                    "SELECT value FROM schema_meta "
                    "WHERE key='classification_config_digest'"
                ).fetchone()
                if (stored_digest and
                        str(stored_digest[0]) != classification_digest(config)):
                    raise RuntimeError(
                        "classification_config_changed_requires_explicit_rebuild")
                stored_version = self.db.execute(
                    "SELECT value FROM schema_meta "
                    "WHERE key='classification_classifier_version'"
                ).fetchone()
                if (stored_version and
                        str(stored_version[0]) != CLASSIFIER_VERSION):
                    raise RuntimeError(
                        "classification_version_changed_requires_explicit_rebuild")
            self.ensure_projects(config)
            existing = self.db.execute(
                "SELECT ingest_seq,event_id,source_id,source_seq,source_ref_hmac,"
                "content_hmac,source_content_hmac,occurred_at,session_hmac,"
                "provenance,runtime,projection_version FROM prompt_events "
                "WHERE source_ref_hmac=?", (source_ref_hmac,)
            ).fetchone()
            if existing:
                if not hmac.compare_digest(str(existing["content_hmac"]), content_hmac):
                    raise RuntimeError("source_ref_content_changed")
                source_bound = existing["source_content_hmac"]
                if str(existing["source_id"]) != "hook":
                    if (source_bound is not None and
                            not hmac.compare_digest(
                                str(source_bound), content_hmac)):
                        raise RuntimeError("source_content_binding_changed")
                    if source_bound is None and not authoritative:
                        raise RuntimeError("source_content_binding_missing")
                event_id = str(existing["event_id"])
                stored_authoritative = (
                    str(existing["provenance"]) == "owner" and
                    str(existing["source_id"]) != "hook" and
                    existing["source_content_hmac"] is not None and
                    hmac.compare_digest(
                        str(existing["source_content_hmac"]), content_hmac) and
                    int(existing["projection_version"]) >= SCHEMA_VERSION
                )
                if authoritative and not stored_authoritative:
                    if not reconcile_existing_authority:
                        raise RuntimeError(
                            "historical_authoritative_event_requires_reclassification")
                    reconciled = self._reconcile_existing_authority(
                        existing=existing, text=text, source_id=source_id,
                        source_seq=source_seq,
                        source_ref_hmac=source_ref_hmac,
                        content_hmac=content_hmac,
                        session_hmac=sess_hmac, occurred_at=occurred_at,
                        observed=observed, runtime=runtime,
                    )
                    return reconciled, True
                if authoritative and (
                        str(existing["source_id"]) != source_id or
                        int(existing["source_seq"]) != int(source_seq) or
                        str(existing["session_hmac"]) != sess_hmac or
                        str(existing["occurred_at"]) != str(occurred_at) or
                        str(existing["runtime"]) != str(runtime)):
                    raise RuntimeError("source_authority_metadata_changed")
                if authoritative and classify_assignments:
                    def scoped_existing(domain: str, value: str) -> str:
                        if domain in {"span", "span-bytes", "clause"}:
                            value = event_id + "\x1f" + value
                        return self.keyed(domain, value)
                    _spans, projected, _overflow = project_prompt(text, scoped_existing)
                    self._insert_assignments(
                        event_id=event_id, clauses=projected, cwd=cwd,
                        source_project=source_project, session_hmac=sess_hmac,
                        occurred_at=occurred_at, source_id=source_id,
                        source_seq=source_seq, observed=observed, config=config,
                    )
                return event_id, False
            if source_id != "hook":
                hook = self.db.execute(
                    "SELECT ingest_seq,event_id,source_id,source_seq,source_ref_hmac,"
                    "content_hmac,source_content_hmac,occurred_at,session_hmac,"
                    "provenance,runtime,projection_version FROM prompt_events "
                    "WHERE source_id='hook' AND content_hmac=? "
                    "AND session_hmac=? ORDER BY ingest_seq ASC LIMIT 1",
                    (content_hmac, sess_hmac),
                ).fetchone()
                if hook:
                    if (authoritative and
                            int(hook["projection_version"]) < SCHEMA_VERSION):
                        if not reconcile_existing_authority:
                            raise RuntimeError(
                                "historical_authoritative_event_requires_reclassification")
                        reconciled = self._reconcile_existing_authority(
                            existing=hook, text=text, source_id=source_id,
                            source_seq=source_seq,
                            source_ref_hmac=source_ref_hmac,
                            content_hmac=content_hmac,
                            session_hmac=sess_hmac,
                            occurred_at=occurred_at, observed=observed,
                            runtime=runtime,
                        )
                        return reconciled, True
                    if (authoritative and classify_assignments and
                            self._later_authoritative_event_exists(
                                event_id=str(hook["event_id"]),
                                session_hmac=sess_hmac,
                                occurred_at=occurred_at, source_id=source_id,
                                source_seq=source_seq)):
                        raise RuntimeError(
                            "historical_authoritative_event_requires_reclassification")
                    self.db.execute(
                        "UPDATE prompt_events SET source_id=?,source_seq=?,source_ref_hmac=?,"
                        "source_content_hmac=?,occurred_at=?,provenance=?,runtime=? WHERE event_id=?",
                        (source_id, int(source_seq), source_ref_hmac, content_hmac,
                         occurred_at, normalized_provenance, runtime,
                         hook["event_id"]),
                    )
                    event_id = str(hook["event_id"])
                    if authoritative and classify_assignments:
                        def scoped_rebound(domain: str, value: str) -> str:
                            if domain in {"span", "span-bytes", "clause"}:
                                value = event_id + "\x1f" + value
                            return self.keyed(domain, value)
                        _spans, projected, _overflow = project_prompt(text, scoped_rebound)
                        self._insert_assignments(
                            event_id=event_id, clauses=projected, cwd=cwd,
                            source_project=source_project, session_hmac=sess_hmac,
                            occurred_at=occurred_at, source_id=source_id,
                            source_seq=source_seq, observed=observed, config=config,
                        )
                    return event_id, False
            event_id = "prompt:" + self.keyed(
                "prompt-event", "%s:%s:%s:%s" % (source_id, source_seq, sess_hmac, content_hmac)
            )[:32]
            if (authoritative and classify_assignments and
                    self._later_authoritative_event_exists(
                        event_id=event_id, session_hmac=sess_hmac,
                        occurred_at=occurred_at or observed, source_id=source_id,
                        source_seq=source_seq)):
                raise RuntimeError(
                    "historical_authoritative_event_requires_reclassification")
            clauses = self._insert_projected_event(
                event_id=event_id, text=text, source_id=source_id,
                source_seq=source_seq, source_ref_hmac=source_ref_hmac,
                content_hmac=content_hmac, occurred_at=occurred_at,
                observed=observed, session_hmac=sess_hmac,
                provenance=normalized_provenance, runtime=runtime,
            )
            if authoritative and classify_assignments:
                self._insert_assignments(
                    event_id=event_id, clauses=clauses, cwd=cwd,
                    source_project=source_project, session_hmac=sess_hmac,
                    occurred_at=occurred_at or observed, source_id=source_id,
                    source_seq=source_seq, observed=observed, config=config,
                )
            return event_id, True

    def active_project_ids(self) -> list[str]:
        return [str(row[0]) for row in self.db.execute(
            "SELECT project_id FROM projects WHERE active=1 ORDER BY pinned DESC,project_id"
        )]

    def latest_review(self, project_id: str):
        return self.db.execute(
            "SELECT rw.*,prs.attempt_generation,prs.published_generation,prs.last_error_code "
            "FROM project_review_state prs LEFT JOIN review_windows rw ON rw.window_id=("
            "SELECT window_id FROM review_windows WHERE project_id=prs.project_id ORDER BY published_at DESC LIMIT 1) "
            "WHERE prs.project_id=?", (project_id,)
        ).fetchone()

    def manifest_digest(self) -> str:
        rows = self.db.execute(
            "SELECT event_id,content_hmac,source_content_hmac,span_count,clause_count,parser_overflow "
            "FROM prompt_events ORDER BY ingest_seq"
        ).fetchall()
        dispositions = self.db.execute(
            "SELECT span_id,event_id,disposition,reason_code FROM coverage_dispositions "
            "ORDER BY event_id,span_id"
        ).fetchall()
        material = "\n".join(":".join(str(value) for value in tuple(row)) for row in rows)
        material += "\n--dispositions--\n" + "\n".join(
            ":".join(str(value) for value in tuple(row)) for row in dispositions
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()
