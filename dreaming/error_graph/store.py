#!/usr/bin/env python3
"""Bounded-latency append-only store for the typed temporal error graph."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple
import sys

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


try:
    from .events import Event, SCHEMA_VERSION, redact, stable_hash
except ImportError:  # direct-script/test execution
    from events import Event, SCHEMA_VERSION, redact, stable_hash

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "schema.sql")
STORE_VERSION = "0.4.0"
MAX_BATCH_EVENTS = 512
MAX_BATCH_AGGREGATES = 512
MAX_AGGREGATE_FEATURES = 32
MAX_AGGREGATE_LINKS = 16

_FORBIDDEN_KEYS = {"text", "raw", "command", "response", "prompt", "snippet", "content", "secret", "password"}
_FEATURE = re.compile(r"^[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_.-]{1,128}$")
_HASH24 = re.compile(r"^[0-9a-f]{24}$")
_SAFE_CLASS = re.compile(r"^[a-z_]{2,24}$")
_SAFE_ORIGIN = re.compile(r"^[a-z_]{2,32}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SAFE_PROMPT_CLASSES = {
    "mistake", "security", "prove", "improve", "think", "run", "remember", "admin",
    "automation", "other",
}
_SAFE_PROMPT_ORIGINS = {"owner", "hook", "automation", "backfill", "import", "unknown"}
_AGGREGATE_RELATIONS = {"CLASS_HAS_LEXEME", "CLASS_HAS_STRUCTURE", "LEXEME_ADJACENT"}
_AGGREGATE_PREFIXES = {
    "act", "aggregate", "ast", "call", "callh", "claim", "code", "context",
    "evidence", "gate", "horizon", "knowledge", "lexh", "magnitude", "occurrences",
    "op", "origin", "path", "polarity", "privacy", "prompt_class", "quantity", "risk",
    "stakes", "sub", "targeth", "unit",
}
_AGGREGATE_SAFE_FEATURES = {
    "aggregate:prompt", "occurrences:1", "occurrences:2_4", "occurrences:5_19",
    "occurrences:20_plus", "act:question", "act:recommend", "act:complete", "act:verify",
    "act:hedge", "act:negate", "act:discussion", "act:quote", "act:assert",
    "knowledge:retraction_context", "stakes:high", "gate:discussion_only", "claim:quantified",
    "quantity:money", "quantity:percent", "unit:usd", "unit:percent", "polarity:negative",
    "horizon:day", "horizon:month", "horizon:year", "op:read", "op:write", "op:mutate",
    "op:execute", "op:test", "op:verify", "op:brain_recall", "op:delete", "op:git_change",
    "op:external", "risk:irreversible", "risk:path_dialect_mismatch",
    "risk:possible_temporal_leakage", "risk:destructive_code", "risk:dynamic_execution",
    "evidence:ground_truth", "evidence:observation", "evidence:artifact_observed",
    "context:fixture", "code:python", "code:parsed", "code:unparsed", "path:windows",
    "privacy:redacted",
}
_AGGREGATE_SAFE_FEATURES.update("prompt_class:" + item for item in _SAFE_PROMPT_CLASSES)
_AGGREGATE_SAFE_FEATURES.update("origin:" + item for item in _SAFE_PROMPT_ORIGINS)
_AGGREGATE_SAFE_FEATURES.update("ast:" + item for item in {
    "call", "compare", "subscript", "import", "importfrom", "with", "try", "lambda",
    "listcomp", "setcomp", "dictcomp", "generatorexp", "await", "delete", "assign",
    "augassign", "return", "raise",
})
_AGGREGATE_SAFE_FEATURES.update("call:" + item for item in {
    "open", "eval", "exec", "compile", "remove", "unlink", "rmtree", "run", "popen",
    "system", "read_csv", "read_parquet", "merge", "join", "shift", "rolling",
})
_WARNING_MODES = {"unknown", "shadow", "advisory", "block"}
_WARNING_INITIAL_STATUSES = {"pending", "candidate"}
_TRANSIENT_GRAPH_PREFIXES = ("lex:", "lexh:", "sub:")


class PrivacyViolation(ValueError):
    pass


class AggregateConflictError(ValueError):
    """An aggregate id was reused for a different privacy-safe payload."""


@dataclass(frozen=True)
class PromptAggregate:
    """One collapsed PROMPTS ledger row, explicitly without temporal fields."""

    aggregate_id: str
    source_hash: str
    source_ref_hash: str
    occurrence_count: int
    prompt_class: str
    origin_kind: str
    features: Tuple[str, ...]
    links: Tuple[Tuple[str, str, str], ...]
    parser_version: str

    def public_record(self) -> Dict[str, Any]:
        return {
            "aggregate_id": self.aggregate_id,
            "source_hash": self.source_hash,
            "source_ref_hash": self.source_ref_hash,
            "occurrence_count": self.occurrence_count,
            "prompt_class": self.prompt_class,
            "origin_kind": self.origin_kind,
            "features": list(self.features),
            "links": [list(link) for link in self.links],
            "parser_version": self.parser_version,
        }


def _privacy_safe(value: Any, path: str = "") -> None:
    """Reject accidental raw fields and secret-looking values before SQLite."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            skey = str(key).lower()
            if skey in _FORBIDDEN_KEYS or skey.endswith("_text") or skey.startswith("raw_"):
                raise PrivacyViolation("forbidden persistence field: %s%s" % (path, key))
            _privacy_safe(child, path + str(key) + ".")
    elif isinstance(value, (list, tuple, set)):
        for i, child in enumerate(value):
            _privacy_safe(child, path + "%d." % i)
    elif isinstance(value, str):
        redacted, hits = redact(value)
        if hits or redacted != value:
            raise PrivacyViolation("secret-like persisted value at %s" % (path or "value"))
        if len(value) > 1024:
            raise PrivacyViolation("oversized persisted string at %s" % (path or "value"))


def _safe_json(value: Any) -> str:
    _privacy_safe(value)
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _node_id(kind: str, label: str) -> str:
    return stable_hash(kind + "|" + label, "node")


def _materialized_graph_features(features: Iterable[str]) -> Tuple[str, ...]:
    """Keep lexical hashes in event JSON, not as duplicated graph fanout.

    Candidate fitting reads ``events.features_json`` directly.  The live
    propagation engine also excludes lexical/subword seeds.  Materializing the
    same hashes again as one node and one edge per event/clause consumed most
    of the shadow store without adding warning signal.
    """
    return tuple(feature for feature in features
                 if not str(feature).startswith(_TRANSIENT_GRAPH_PREFIXES))


def _aggregate_feature_kind(feature: str) -> str:
    prefix = feature.split(":", 1)[0]
    if prefix not in _AGGREGATE_PREFIXES or not _FEATURE.fullmatch(feature):
        raise PrivacyViolation("unsafe aggregate feature")
    if prefix == "lexh":
        if not re.fullmatch(r"lexh:[0-9a-f]{24}", feature):
            raise PrivacyViolation("aggregate lexical features must be hashed")
        return "lexical"
    if prefix == "sub":
        if not re.fullmatch(r"sub:[0-9a-f]{12}", feature):
            raise PrivacyViolation("aggregate subword features must be hashed")
        return "subword"
    if prefix == "callh" and not re.fullmatch(r"callh:[0-9a-f]{12}", feature):
        raise PrivacyViolation("aggregate unknown calls must be hashed")
    if prefix == "targeth" and not re.fullmatch(r"targeth:[0-9a-f]{16}", feature):
        raise PrivacyViolation("aggregate targets must be hashed")
    if prefix == "magnitude":
        if not re.fullmatch(r"magnitude:(?:zero|1e-?[0-9]{1,2})", feature):
            raise PrivacyViolation("aggregate magnitude is not a bounded bucket")
    elif prefix not in {"lexh", "sub", "callh", "targeth"} and feature not in _AGGREGATE_SAFE_FEATURES:
        raise PrivacyViolation("aggregate semantic label is not in the public taxonomy")
    if prefix in {"code", "ast", "call", "callh"}:
        return "code"
    if prefix == "prompt_class":
        return "class"
    if prefix == "origin":
        return "origin"
    return "structural"


def _prepare_aggregate(value: PromptAggregate | Mapping[str, Any]) -> Dict[str, Any]:
    record = value.public_record() if isinstance(value, PromptAggregate) else dict(value)
    aggregate_id = str(record.get("aggregate_id") or "")
    source_hash = str(record.get("source_hash") or "")
    source_ref_hash = str(record.get("source_ref_hash") or "")
    if not all(_HASH24.fullmatch(item) for item in (aggregate_id, source_hash, source_ref_hash)):
        raise PrivacyViolation("aggregate provenance must use 24-hex hashes")
    try:
        occurrence_count = int(record.get("occurrence_count"))
    except (TypeError, ValueError) as exc:
        raise ValueError("aggregate occurrence_count is invalid") from exc
    if not 1 <= occurrence_count <= 9_223_372_036_854_775_807:
        raise ValueError("aggregate occurrence_count is outside SQLite INTEGER range")
    prompt_class = str(record.get("prompt_class") or "")
    origin_kind = str(record.get("origin_kind") or "")
    parser_version = str(record.get("parser_version") or "")
    if not _SAFE_CLASS.fullmatch(prompt_class) or prompt_class not in _SAFE_PROMPT_CLASSES:
        raise PrivacyViolation("aggregate prompt class is not a safe label")
    if not _SAFE_ORIGIN.fullmatch(origin_kind) or origin_kind not in _SAFE_PROMPT_ORIGINS:
        raise PrivacyViolation("aggregate origin is not a safe label")
    if not _SAFE_VERSION.fullmatch(parser_version):
        raise PrivacyViolation("aggregate parser version is not a safe label")

    raw_features = tuple(str(item) for item in record.get("features") or ())
    if len(raw_features) > MAX_AGGREGATE_FEATURES or len(raw_features) != len(set(raw_features)):
        raise ValueError("aggregate feature set is duplicate or over its bound")
    features = tuple(sorted(raw_features))
    feature_kinds = {feature: _aggregate_feature_kind(feature) for feature in features}
    required = {"aggregate:prompt", "prompt_class:" + prompt_class, "origin:" + origin_kind}
    if not required.issubset(feature_kinds):
        raise ValueError("aggregate is missing its class/origin anchors")

    raw_links = tuple(tuple(str(part) for part in item) for item in record.get("links") or ())
    if len(raw_links) > MAX_AGGREGATE_LINKS or len(raw_links) != len(set(raw_links)):
        raise ValueError("aggregate link set is duplicate or over its bound")
    links = tuple(sorted(raw_links))
    for link in links:
        if len(link) != 3:
            raise ValueError("aggregate links must contain src, dst, relation")
        src, dst, relation = link
        if src not in feature_kinds or dst not in feature_kinds or relation not in _AGGREGATE_RELATIONS:
            raise ValueError("aggregate link references an unsafe endpoint or relation")
    public = {
        "aggregate_id": aggregate_id,
        "source_hash": source_hash,
        "source_ref_hash": source_ref_hash,
        "occurrence_count": occurrence_count,
        "prompt_class": prompt_class,
        "origin_kind": origin_kind,
        "features": list(features),
        "links": [list(link) for link in links],
        "parser_version": parser_version,
    }
    _privacy_safe(public)
    public["feature_kinds"] = feature_kinds
    public["payload_hash"] = stable_hash(_safe_json(public), "prompt-aggregate-payload")
    return public


def _warning_control(warning: Mapping[str, Any]) -> Tuple[str, str]:
    mode = str(warning.get("mode") or "unknown")
    status = str(warning.get("status") or "pending")
    if mode not in _WARNING_MODES:
        raise ValueError("invalid warning mode")
    if status not in _WARNING_INITIAL_STATUSES:
        raise ValueError("invalid initial warning status")
    if mode == "shadow" and status != "candidate":
        raise ValueError("shadow warnings must remain unexposed candidates")
    return mode, status


class GraphStore:
    """Short-transaction SQLite store.

    Runtime calls fail open: mutating methods return False/None on lock or I/O
    errors when ``fail_open`` is true.  Tests and offline builders set it false.
    """

    def __init__(self, path: str, timeout_ms: int = 60, fail_open: bool = True, initialize: bool = True):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.timeout_ms = max(1, int(timeout_ms))
        self.fail_open = bool(fail_open)
        if initialize:
            self.initialize()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=self.timeout_ms / 1000.0, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=%d" % self.timeout_ms)
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def initialize(self) -> bool:
        try:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(SCHEMA_PATH, encoding="utf-8") as handle:
                schema = handle.read()
            con = self._connect()
            try:
                # WAL permits concurrent hook readers while one short append is active.
                con.execute("PRAGMA journal_mode=WAL")
                con.executescript(schema)
                warning_columns = {str(row["name"]) for row in con.execute("PRAGMA table_info(warnings)")}
                if "mode" not in warning_columns:
                    con.execute("ALTER TABLE warnings ADD COLUMN mode TEXT NOT NULL DEFAULT 'unknown'")
                con.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('store_version',?)", (STORE_VERSION,))
                con.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
            finally:
                con.close()
            return True
        except Exception:
            if self.fail_open:
                return False
            raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            con.close()

    @staticmethod
    def _add_node(con: sqlite3.Connection, kind: str, label: str, ts: float,
                  attrs: Optional[Mapping[str, Any]] = None) -> str:
        label = str(label)[:160]
        safe_label = label
        redacted, hits = redact(label)
        if hits or redacted != label or "\\" in label or ("/" in label and not label.startswith(("surface:", "event:"))):
            safe_label = "hash:" + stable_hash(label, "node-label")
        node_id = _node_id(kind, safe_label)
        con.execute(
            "INSERT OR IGNORE INTO nodes(node_id,kind,label,label_hash,attrs_json,created_at) VALUES(?,?,?,?,?,?)",
            (node_id, kind, safe_label, stable_hash(label, "label"), _safe_json(dict(attrs or {})), float(ts)),
        )
        return node_id

    @staticmethod
    def _add_edge(con: sqlite3.Connection, *, src: str, dst: str, relation: str, event: Event,
                  weight: float = 1.0, confidence: float = 1.0, polarity: int = 1,
                  evidence_quality: str = "observed", model_version: str = STORE_VERSION,
                  attrs: Optional[Mapping[str, Any]] = None) -> str:
        edge_id = stable_hash("|".join((src, dst, relation, event.event_id, model_version)), "edge")
        con.execute(
            """INSERT OR IGNORE INTO edges(
               edge_id,src_id,dst_id,relation,event_ts,observed_ts,weight,confidence,polarity,
               source_ref_hash,episode_id,actor_hash,surface,evidence_quality,valid_from,valid_to,
               superseded,model_version,attrs_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (edge_id, src, dst, relation, event.event_ts, event.observed_ts, float(weight),
             max(0.0, min(1.0, float(confidence))), 1 if polarity >= 0 else -1,
             event.source_ref_hash, event.episode_id, event.actor_hash, event.surface,
             evidence_quality, event.event_ts, None, 0, model_version, _safe_json(dict(attrs or {}))),
        )
        return edge_id

    @staticmethod
    def _event_from_row(row: Mapping[str, Any]) -> Event:
        """Rehydrate only persistence-safe fields for temporal-edge repair."""
        return Event(
            event_id=str(row["event_id"]), event_ts=float(row["event_ts"]),
            observed_ts=float(row["observed_ts"]), session_hash=str(row["session_hash"]),
            actor_hash=str(row["actor_hash"]), episode_id=str(row["episode_id"]),
            surface=str(row["surface"]), kind=str(row["kind"]),
            source_ref_hash=str(row["source_ref_hash"]),
            action_fingerprint=str(row["action_fingerprint"]),
            features=tuple(json.loads(row["features_json"] or "[]")),
            clauses=tuple(tuple(item) for item in json.loads(row["clauses_json"] or "[]")),
            attrs=json.loads(row["attrs_json"] or "{}"), private={},
        )

    def _record_event_in_transaction(self, con: sqlite3.Connection, event: Event,
                                     validate_public: bool = True) -> bool:
        """Apply one event using an existing transaction.

        Keeping this as the single implementation makes offline batches and the
        live one-event path byte-for-byte equivalent for a fixed event order.
        Batch privacy checks deliberately run inside the transaction so a bad
        event rolls back every earlier event in that bounded chunk.  The live
        caller validates before acquiring the write lock to preserve its short
        critical section.
        """
        if validate_public:
            _privacy_safe(event.public_record())
        con.execute(
            "INSERT OR IGNORE INTO episodes(episode_id,session_hash,task_hash,started_at,attrs_json) VALUES(?,?,?,?,?)",
            (event.episode_id, event.session_hash, None, event.event_ts, "{}"),
        )
        inserted = con.execute(
            """INSERT OR IGNORE INTO events(
               event_id,event_ts,observed_ts,session_hash,actor_hash,episode_id,surface,kind,
               source_ref_hash,action_fingerprint,features_json,clauses_json,attrs_json,schema_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event.event_id, event.event_ts, event.observed_ts, event.session_hash, event.actor_hash,
             event.episode_id, event.surface, event.kind, event.source_ref_hash,
             event.action_fingerprint, _safe_json(list(event.features)),
             _safe_json([list(c) for c in event.clauses]), _safe_json(dict(event.attrs)), SCHEMA_VERSION),
        ).rowcount == 1
        if not inserted:
            return False
        event_node = self._add_node(con, "event", "event:" + event.event_id, event.event_ts,
                                    {"surface": event.surface, "kind": event.kind})
        session_node = self._add_node(con, "session", "session:" + event.session_hash, event.event_ts)
        actor_node = self._add_node(con, "agent", "agent:" + event.actor_hash, event.event_ts)
        surface_node = self._add_node(con, "surface", "surface:" + event.surface, event.event_ts)
        for node_id, role in ((event_node, "event"), (session_node, "session"),
                              (actor_node, "actor"), (surface_node, "surface")):
            con.execute("INSERT OR IGNORE INTO episode_members(episode_id,node_id,role,event_ts) VALUES(?,?,?,?)",
                        (event.episode_id, node_id, role, event.event_ts))
        self._add_edge(con, src=event_node, dst=session_node, relation="IN_SESSION", event=event)
        self._add_edge(con, src=event_node, dst=actor_node, relation="ATTRIBUTED_TO", event=event)
        self._add_edge(con, src=event_node, dst=surface_node, relation="OBSERVED_ON", event=event)

        for feature in _materialized_graph_features(event.features):
            kind = feature.split(":", 1)[0] if ":" in feature else "feature"
            feature_node = self._add_node(con, kind, feature, event.event_ts)
            self._add_edge(con, src=event_node, dst=feature_node, relation="HAS_FEATURE", event=event)
            con.execute("INSERT OR IGNORE INTO episode_members(episode_id,node_id,role,event_ts) VALUES(?,?,?,?)",
                        (event.episode_id, feature_node, "feature", event.event_ts))

        # Clause nodes preserve local assertion/negation/quantity scope.
        for index, clause_features in enumerate(event.clauses):
            graph_features = _materialized_graph_features(clause_features)
            if not graph_features:
                continue
            clause_label = "clause:%s:%d" % (event.event_id, index)
            clause_node = self._add_node(con, "clause", clause_label, event.event_ts,
                                         {"index": index, "features": list(graph_features)})
            self._add_edge(con, src=event_node, dst=clause_node, relation="CONTAINS", event=event)
            con.execute("INSERT OR IGNORE INTO episode_members(episode_id,node_id,role,event_ts) VALUES(?,?,?,?)",
                        (event.episode_id, clause_node, "clause", event.event_ts))
            for feature in graph_features:
                feature_node = self._add_node(con, feature.split(":", 1)[0], feature, event.event_ts)
                self._add_edge(con, src=clause_node, dst=feature_node, relation="HAS_FEATURE", event=event)

        previous = con.execute(
            """SELECT event_id,event_ts FROM events
               WHERE session_hash=? AND (event_ts<? OR (event_ts=? AND event_id<?))
               ORDER BY event_ts DESC,event_id DESC LIMIT 1""",
            (event.session_hash, event.event_ts, event.event_ts, event.event_id),
        ).fetchone()
        successor = con.execute(
            """SELECT * FROM events
               WHERE session_hash=? AND (event_ts>? OR (event_ts=? AND event_id>?))
               ORDER BY event_ts,event_id LIMIT 1""",
            (event.session_hash, event.event_ts, event.event_ts, event.event_id),
        ).fetchone()
        if previous:
            previous_node = self._add_node(con, "event", "event:" + previous["event_id"], previous["event_ts"])
            self._add_edge(con, src=previous_node, dst=event_node, relation="PRECEDES_WITHIN", event=event,
                           attrs={"lag_seconds": max(0.0, event.event_ts - float(previous["event_ts"]))})
        if successor:
            successor_event = self._event_from_row(successor)
            successor_node = self._add_node(con, "event", "event:" + successor_event.event_id,
                                            successor_event.event_ts)
            if previous:
                con.execute("DELETE FROM edges WHERE src_id=? AND dst_id=? AND relation='PRECEDES_WITHIN'",
                            (previous_node, successor_node))
            self._add_edge(con, src=event_node, dst=successor_node, relation="PRECEDES_WITHIN",
                           event=successor_event,
                           attrs={"lag_seconds": max(0.0, successor_event.event_ts - event.event_ts)})
        return True

    def record_event(self, event: Event) -> Optional[bool]:
        """Append once in one short live transaction.

        Return True when inserted, False for a duplicate, and None on a
        fail-open error.  Hooks continue to use this latency-bounded path.
        """
        try:
            # Keep potentially expensive whole-record validation outside the
            # latency-sensitive live write transaction, as before batching.
            _privacy_safe(event.public_record())
            with self._transaction() as con:
                return self._record_event_in_transaction(con, event, validate_public=False)
        except Exception:
            if self.fail_open:
                return None
            raise

    def record_events(self, events: Iterable[Event]) -> Optional[List[bool]]:
        """Atomically append one bounded offline chunk.

        The hard cap prevents an accidental all-corpus transaction.  A failure
        rolls back the complete chunk; with ``fail_open=True`` it returns None,
        matching ``record_event`` rather than reporting partial success.
        """
        results: List[bool] = []
        try:
            with self._transaction() as con:
                for index, event in enumerate(events):
                    if index >= MAX_BATCH_EVENTS:
                        raise ValueError("offline event batch exceeds %d" % MAX_BATCH_EVENTS)
                    results.append(self._record_event_in_transaction(con, event))
            return results
        except Exception:
            if self.fail_open:
                return None
            raise

    @staticmethod
    def _record_prompt_aggregate_in_transaction(
            con: sqlite3.Connection, value: PromptAggregate | Mapping[str, Any]) -> bool:
        record = _prepare_aggregate(value)
        existing = con.execute(
            "SELECT payload_hash FROM prompt_aggregates WHERE aggregate_id=?",
            (record["aggregate_id"],),
        ).fetchone()
        if existing:
            if str(existing["payload_hash"]) != record["payload_hash"]:
                raise AggregateConflictError("aggregate id already has a different payload")
            return False
        con.execute(
            """INSERT INTO prompt_aggregates(
               aggregate_id,source_hash,source_ref_hash,occurrence_count,prompt_class,origin_kind,
               feature_count,link_count,payload_hash,parser_version) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (record["aggregate_id"], record["source_hash"], record["source_ref_hash"],
             record["occurrence_count"], record["prompt_class"], record["origin_kind"],
             len(record["features"]), len(record["links"]), record["payload_hash"],
             record["parser_version"]),
        )
        feature_ids: Dict[str, str] = {}
        for feature in record["features"]:
            feature_id = stable_hash(feature, "aggregate-feature")
            feature_ids[feature] = feature_id
            con.execute(
                "INSERT OR IGNORE INTO aggregate_feature_catalog(feature_id,feature,feature_kind) VALUES(?,?,?)",
                (feature_id, feature, record["feature_kinds"][feature]),
            )
            catalog = con.execute(
                "SELECT feature,feature_kind FROM aggregate_feature_catalog WHERE feature_id=?", (feature_id,),
            ).fetchone()
            if (not catalog or str(catalog["feature"]) != feature
                    or str(catalog["feature_kind"]) != record["feature_kinds"][feature]):
                raise AggregateConflictError("aggregate feature hash collision")
            con.execute(
                """INSERT INTO prompt_aggregate_members(
                   aggregate_id,feature_id,role,occurrence_weight) VALUES(?,?,?,?)""",
                (record["aggregate_id"], feature_id, record["feature_kinds"][feature],
                 record["occurrence_count"]),
            )
        for src, dst, relation in record["links"]:
            con.execute(
                """INSERT INTO prompt_aggregate_links(
                   aggregate_id,src_feature_id,dst_feature_id,relation,occurrence_weight)
                   VALUES(?,?,?,?,?)""",
                (record["aggregate_id"], feature_ids[src], feature_ids[dst], relation,
                 record["occurrence_count"]),
            )
        return True

    def record_prompt_aggregate(
            self, aggregate: PromptAggregate | Mapping[str, Any]) -> Optional[bool]:
        """Store one collapsed ledger row without creating a temporal event."""
        result = self.record_prompt_aggregates((aggregate,))
        return None if result is None else result[0]

    def record_prompt_aggregates(
            self, aggregates: Iterable[PromptAggregate | Mapping[str, Any]]) -> Optional[List[bool]]:
        """Atomically append one bounded batch of compact prompt aggregates."""
        results: List[bool] = []
        try:
            with self._transaction() as con:
                for index, aggregate in enumerate(aggregates):
                    if index >= MAX_BATCH_AGGREGATES:
                        raise ValueError("offline aggregate batch exceeds %d" % MAX_BATCH_AGGREGATES)
                    results.append(self._record_prompt_aggregate_in_transaction(con, aggregate))
            return results
        except Exception:
            if self.fail_open:
                return None
            raise

    def recent_events(self, session_hash: str, limit: int = 32, before: Optional[float] = None,
                      observed_before: Optional[float] = None,
                      before_event_id: Optional[str] = None) -> List[Dict[str, Any]]:
        try:
            con = self._connect()
            try:
                if before is None:
                    rows = con.execute(
                        "SELECT * FROM events WHERE session_hash=? ORDER BY event_ts DESC,event_id DESC LIMIT ?",
                        (session_hash, max(1, min(512, int(limit)))),
                    ).fetchall()
                else:
                    tie_id = str(before_event_id or "\uffff")
                    observed = float("inf") if observed_before is None else float(observed_before)
                    rows = con.execute(
                        """SELECT * FROM events WHERE session_hash=?
                           AND (event_ts<? OR (event_ts=? AND event_id<?)) AND observed_ts<=?
                           ORDER BY event_ts DESC,event_id DESC LIMIT ?""",
                        (session_hash, float(before), float(before), tie_id, observed,
                         max(1, min(512, int(limit)))),
                    ).fetchall()
                out = []
                for row in reversed(rows):
                    item = dict(row)
                    item["features"] = json.loads(item.pop("features_json"))
                    item["clauses"] = json.loads(item.pop("clauses_json"))
                    item["attrs"] = json.loads(item.pop("attrs_json"))
                    out.append(item)
                return out
            finally:
                con.close()
        except Exception:
            if self.fail_open:
                return []
            raise

    def record_warning(self, warning: Mapping[str, Any]) -> bool:
        try:
            allowed = {"trace_id", "event_id", "session_hash", "action_fingerprint", "family", "score", "tier", "reason_code",
                       "evidence", "remediation_code", "model_version", "created_at", "mode", "status"}
            if set(warning) - allowed:
                raise PrivacyViolation("unexpected warning fields: %s" % sorted(set(warning) - allowed))
            _privacy_safe(warning)
            mode, status = _warning_control(warning)
            with self._transaction() as con:
                con.execute(
                    """INSERT OR IGNORE INTO warnings(trace_id,event_id,session_hash,action_fingerprint,family,score,tier,
                       reason_code,evidence_json,remediation_code,model_version,created_at,mode,status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (warning["trace_id"], warning["event_id"], warning["session_hash"], warning["action_fingerprint"], warning["family"],
                     float(warning["score"]), warning["tier"], warning["reason_code"],
                     _safe_json(warning.get("evidence") or []), warning["remediation_code"],
                     warning["model_version"], float(warning.get("created_at") or time.time()), mode, status),
                )
            return True
        except Exception:
            if self.fail_open:
                return False
            raise

    def record_warning_if_fresh(self, warning: Mapping[str, Any], refractory_seconds: float,
                                burden_bump: float = 1.0) -> bool:
        """Atomically insert a warning and consume its refractory slot."""
        try:
            allowed = {"trace_id", "event_id", "session_hash", "action_fingerprint", "family", "score", "tier",
                       "reason_code", "evidence", "remediation_code", "model_version", "created_at", "mode", "status"}
            if set(warning) - allowed:
                raise PrivacyViolation("unexpected warning fields: %s" % sorted(set(warning) - allowed))
            _privacy_safe(warning)
            mode, status = _warning_control(warning)
            with self._transaction() as con:
                row = con.execute(
                    "SELECT last_fire_ts,warning_burden FROM family_state WHERE session_hash=? AND family=?",
                    (warning["session_hash"], warning["family"]),
                ).fetchone()
                if not row:
                    return False
                created = float(warning.get("created_at") or time.time())
                last_fire = row["last_fire_ts"]
                if last_fire is not None and created - float(last_fire) < float(refractory_seconds):
                    return False
                inserted = con.execute(
                    """INSERT OR IGNORE INTO warnings(trace_id,event_id,session_hash,action_fingerprint,family,score,tier,
                       reason_code,evidence_json,remediation_code,model_version,created_at,mode,status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (warning["trace_id"], warning["event_id"], warning["session_hash"],
                     warning["action_fingerprint"], warning["family"], float(warning["score"]), warning["tier"],
                     warning["reason_code"], _safe_json(warning.get("evidence") or []),
                     warning["remediation_code"], warning["model_version"], created, mode, status),
                ).rowcount == 1
                if not inserted:
                    return False
                con.execute(
                    "UPDATE family_state SET last_fire_ts=?,warning_burden=? WHERE session_hash=? AND family=?",
                    (created, min(8.0, float(row["warning_burden"] or 0.0) + float(burden_bump)),
                     warning["session_hash"], warning["family"]),
                )
            return True
        except Exception:
            if self.fail_open:
                return False
            raise

    def pending_warnings(self, session_hash: str, limit: int = 16) -> List[Dict[str, Any]]:
        try:
            con = self._connect()
            try:
                rows = con.execute(
                    """SELECT w.*,e.episode_id AS warning_episode_id,e.surface AS warning_surface,
                              e.features_json AS warning_features_json
                       FROM warnings w JOIN events e ON e.event_id=w.event_id
                       WHERE w.session_hash=? AND w.status='pending'
                       ORDER BY w.created_at DESC LIMIT ?""",
                    (session_hash, max(1, min(100, int(limit)))),
                ).fetchall()
                out = []
                for row in rows:
                    item = dict(row)
                    item["evidence"] = json.loads(item.pop("evidence_json"))
                    item["warning_features"] = json.loads(item.pop("warning_features_json") or "[]")
                    out.append(item)
                return out
            finally:
                con.close()
        except Exception:
            if self.fail_open:
                return []
            raise

    def candidate_warnings(self, session_hash: str, action_fingerprint: str,
                           limit: int = 16) -> List[Dict[str, Any]]:
        """Return unexposed candidates eligible for a prospective outcome join."""
        if not action_fingerprint:
            return []
        try:
            con = self._connect()
            try:
                rows = con.execute(
                    """SELECT * FROM warnings WHERE session_hash=? AND action_fingerprint=?
                       AND status='candidate' ORDER BY created_at DESC LIMIT ?""",
                    (session_hash, action_fingerprint, max(1, min(100, int(limit)))),
                ).fetchall()
                out = []
                for row in rows:
                    item = dict(row)
                    item["evidence"] = json.loads(item.pop("evidence_json"))
                    out.append(item)
                return out
            finally:
                con.close()
        except Exception:
            if self.fail_open:
                return []
            raise

    def resolve_candidate_with_outcome(self, trace_id: str, event: Event, outcome: str) -> bool:
        """Join a hidden candidate to observed success/failure without causal credit."""
        if outcome not in {"success", "failure"}:
            raise ValueError("invalid candidate outcome")
        try:
            status = "candidate_" + outcome
            attrs = {"reason_code": "prospective_" + outcome, "exposure": "unexposed"}
            with self._transaction() as con:
                changed = con.execute(
                    """UPDATE warnings SET status=?,resolved_event_id=?,resolution_ts=?
                       WHERE trace_id=? AND session_hash=? AND status='candidate'""",
                    (status, event.event_id, event.observed_ts, trace_id, event.session_hash),
                ).rowcount == 1
                if not changed:
                    return False
                outcome_id = stable_hash("|".join((event.event_id, status, trace_id)), "outcome")
                con.execute(
                    """INSERT OR IGNORE INTO outcomes(outcome_id,event_id,session_hash,kind,severity,
                       warning_trace_id,adjudication,created_at,attrs_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (outcome_id, event.event_id, event.session_hash, status,
                     0.5 if outcome == "failure" else 0.0, trace_id,
                     "prospective_unexposed", event.observed_ts, _safe_json(attrs)),
                )
            return True
        except Exception:
            if self.fail_open:
                return False
            raise

    def resolve_warning(self, trace_id: str, status: str, event_id: str, ts: Optional[float] = None) -> bool:
        if status not in {"heeded", "ignored", "superseded", "unknown"}:
            raise ValueError("invalid warning status")
        try:
            with self._transaction() as con:
                changed = con.execute(
                    "UPDATE warnings SET status=?,resolved_event_id=?,resolution_ts=? WHERE trace_id=? AND status='pending'",
                    (status, event_id, float(ts or time.time()), trace_id),
                ).rowcount == 1
            return changed
        except Exception:
            if self.fail_open:
                return False
            raise

    def resolve_warning_with_outcome(self, trace_id: str, status: str, event: Event,
                                     reason_code: str) -> bool:
        """Resolve exactly once and append its resolution outcome in the same transaction."""
        if status not in {"heeded", "ignored", "superseded", "unknown"}:
            raise ValueError("invalid warning status")
        try:
            attrs = {"reason_code": str(reason_code)}
            _privacy_safe(attrs)
            with self._transaction() as con:
                changed = con.execute(
                    """UPDATE warnings SET status=?,resolved_event_id=?,resolution_ts=?
                       WHERE trace_id=? AND session_hash=? AND status='pending'""",
                    (status, event.event_id, event.observed_ts, trace_id, event.session_hash),
                ).rowcount == 1
                if not changed:
                    return False
                kind = "warning_" + status
                outcome_id = stable_hash("|".join((event.event_id, kind, trace_id)), "outcome")
                con.execute(
                    """INSERT OR IGNORE INTO outcomes(outcome_id,event_id,session_hash,kind,severity,
                       warning_trace_id,adjudication,created_at,attrs_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (outcome_id, event.event_id, event.session_hash, kind, 0.0, trace_id,
                     "observed", event.observed_ts, _safe_json(attrs)),
                )
            return True
        except Exception:
            if self.fail_open:
                return False
            raise

    def record_outcome(self, event: Event, kind: str, severity: float = 0.0,
                       warning_trace_id: Optional[str] = None, adjudication: str = "observed",
                       attrs: Optional[Mapping[str, Any]] = None) -> Optional[str]:
        try:
            attrs = dict(attrs or {})
            _privacy_safe(attrs)
            outcome_id = stable_hash("|".join((event.event_id, kind, warning_trace_id or "")), "outcome")
            with self._transaction() as con:
                con.execute(
                    """INSERT OR IGNORE INTO outcomes(outcome_id,event_id,session_hash,kind,severity,
                       warning_trace_id,adjudication,created_at,attrs_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (outcome_id, event.event_id, event.session_hash, kind, float(severity), warning_trace_id,
                     adjudication, event.observed_ts, _safe_json(attrs)),
                )
            return outcome_id
        except Exception:
            if self.fail_open:
                return None
            raise

    def get_family_state(self, session_hash: str, family: str) -> Dict[str, Any]:
        default = {"activation": 0.0, "warning_burden": 0.0, "last_ts": 0.0,
                   "last_fire_ts": None, "sequence": {}}
        try:
            con = self._connect()
            try:
                row = con.execute("SELECT * FROM family_state WHERE session_hash=? AND family=?",
                                  (session_hash, family)).fetchone()
                if not row:
                    return default
                return {"activation": float(row["activation"]), "warning_burden": float(row["warning_burden"]),
                        "last_ts": float(row["last_ts"]), "last_fire_ts": row["last_fire_ts"],
                        "sequence": json.loads(row["sequence_json"] or "{}")}
            finally:
                con.close()
        except Exception:
            if self.fail_open:
                return default
            raise

    def put_family_state(self, session_hash: str, family: str, state: Mapping[str, Any]) -> bool:
        try:
            sequence = dict(state.get("sequence") or {})
            _privacy_safe(sequence)
            with self._transaction() as con:
                con.execute(
                    """INSERT INTO family_state(session_hash,family,activation,warning_burden,last_ts,last_fire_ts,sequence_json)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_hash,family) DO UPDATE SET
                       activation=excluded.activation,warning_burden=excluded.warning_burden,
                       last_ts=excluded.last_ts,last_fire_ts=excluded.last_fire_ts,sequence_json=excluded.sequence_json""",
                    (session_hash, family, float(state.get("activation") or 0.0),
                     float(state.get("warning_burden") or 0.0), float(state.get("last_ts") or 0.0),
                     state.get("last_fire_ts"), _safe_json(sequence)),
                )
            return True
        except Exception:
            if self.fail_open:
                return False
            raise

    def advance_family_state(self, session_hash: str, family: str, event_ts: float, evidence: float,
                             config: Mapping[str, Any]) -> Dict[str, Any]:
        """Atomically decay and add evidence; prevents same-session lost updates."""
        try:
            with self._transaction() as con:
                row = con.execute("SELECT * FROM family_state WHERE session_hash=? AND family=?",
                                  (session_hash, family)).fetchone()
                previous = dict(row) if row else {}
                last_ts = float(previous.get("last_ts") or event_ts)
                dt = max(0.0, float(event_ts) - last_ts)
                ahl = max(1.0, float(config.get("activation_half_life", 1200.0)))
                bhl = max(1.0, float(config.get("burden_half_life", 900.0)))
                activation = float(previous.get("activation") or 0.0) * (0.5 ** (dt / ahl))
                burden = float(previous.get("warning_burden") or 0.0) * (0.5 ** (dt / bhl))
                activation += float(evidence) - float(config.get("burden_penalty", 0.22)) * burden
                activation = max(-8.0, min(8.0, activation))
                sequence_json = previous.get("sequence_json") or "{}"
                con.execute(
                    """INSERT INTO family_state(session_hash,family,activation,warning_burden,last_ts,last_fire_ts,sequence_json)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(session_hash,family) DO UPDATE SET
                       activation=excluded.activation,warning_burden=excluded.warning_burden,
                       last_ts=excluded.last_ts,sequence_json=excluded.sequence_json""",
                    (session_hash, family, activation, max(0.0, burden), max(last_ts, float(event_ts)),
                     previous.get("last_fire_ts"), sequence_json),
                )
                return {"activation": activation, "warning_burden": max(0.0, burden),
                        "last_ts": max(last_ts, float(event_ts)), "last_fire_ts": previous.get("last_fire_ts"),
                        "sequence": json.loads(sequence_json), "_ok": True}
        except Exception:
            if self.fail_open:
                return {"activation": 0.0, "warning_burden": 0.0, "last_ts": float(event_ts),
                        "last_fire_ts": None, "sequence": {}, "_ok": False}
            raise

    def claim_family_fire(self, session_hash: str, family: str, event_ts: float,
                          refractory_seconds: float, burden_bump: float = 1.0) -> bool:
        """Atomically acquire the per-family refractory slot for one warning."""
        try:
            with self._transaction() as con:
                row = con.execute("SELECT last_fire_ts,warning_burden FROM family_state WHERE session_hash=? AND family=?",
                                  (session_hash, family)).fetchone()
                if not row:
                    return False
                last_fire = row["last_fire_ts"]
                if last_fire is not None and float(event_ts) - float(last_fire) < float(refractory_seconds):
                    return False
                con.execute("UPDATE family_state SET last_fire_ts=?,warning_burden=? WHERE session_hash=? AND family=?",
                            (float(event_ts), min(8.0, float(row["warning_burden"] or 0.0) + float(burden_bump)),
                             session_hash, family))
                return True
        except Exception:
            if self.fail_open:
                return False
            raise

    def counts(self) -> Dict[str, int]:
        tables = ("nodes", "episodes", "events", "edges", "prompt_aggregates",
                  "aggregate_feature_catalog", "prompt_aggregate_members",
                  "prompt_aggregate_links", "warnings", "outcomes", "source_manifest")
        try:
            con = self._connect()
            try:
                return {table: int(con.execute("SELECT count(*) FROM " + table).fetchone()[0]) for table in tables}
            finally:
                con.close()
        except Exception:
            if self.fail_open:
                return {table: 0 for table in tables}
            raise

    def get_source_manifest(self, source_hash: str) -> Optional[Dict[str, Any]]:
        try:
            con = self._connect()
            try:
                row = con.execute("SELECT * FROM source_manifest WHERE source_hash=?", (source_hash,)).fetchone()
                if not row:
                    return None
                item = dict(row)
                item["attrs"] = json.loads(item.pop("attrs_json") or "{}")
                return item
            finally:
                con.close()
        except Exception:
            if self.fail_open:
                return None
            raise

    def put_source_manifest(self, record: Mapping[str, Any]) -> bool:
        try:
            attrs = dict(record.get("attrs") or {})
            _privacy_safe(attrs)
            with self._transaction() as con:
                con.execute(
                    """INSERT INTO source_manifest(source_hash,path_hash,source_kind,size_bytes,mtime_ns,digest,
                       cursor,status,redaction_version,parser_version,last_ingested,attrs_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_hash) DO UPDATE SET
                       path_hash=excluded.path_hash,source_kind=excluded.source_kind,size_bytes=excluded.size_bytes,
                       mtime_ns=excluded.mtime_ns,digest=excluded.digest,cursor=excluded.cursor,status=excluded.status,
                       redaction_version=excluded.redaction_version,parser_version=excluded.parser_version,
                       last_ingested=excluded.last_ingested,attrs_json=excluded.attrs_json""",
                    (record["source_hash"], record["path_hash"], record["source_kind"], int(record["size_bytes"]),
                     int(record["mtime_ns"]), record["digest"], record.get("cursor"), record.get("status", "indexed"),
                     record.get("redaction_version", "1"), record.get("parser_version", "1"),
                     float(record.get("last_ingested") or time.time()), _safe_json(attrs)),
                )
            return True
        except Exception:
            if self.fail_open:
                return False
            raise

    def integrity_report(self) -> Dict[str, Any]:
        try:
            con = self._connect()
            try:
                integrity = con.execute("PRAGMA quick_check").fetchone()[0]
                rawish = con.execute(
                    "SELECT count(*) FROM nodes WHERE length(label)>160 OR label LIKE '%\\%'"
                ).fetchone()[0]
                unsafe_aggregates = con.execute(
                    """SELECT count(*) FROM aggregate_feature_catalog
                       WHERE feature LIKE 'lex:%' OR feature LIKE '%\\%' OR length(feature)>160"""
                ).fetchone()[0]
                return {"integrity": integrity, "rawish_labels": int(rawish),
                        "unsafe_aggregate_features": int(unsafe_aggregates), "counts": self.counts()}
            finally:
                con.close()
        except Exception as exc:
            if self.fail_open:
                return {"integrity": "unavailable", "error_type": type(exc).__name__}
            raise


class MemoryStore:
    """Non-persistent store for dry runs and deterministic replay tests."""

    def __init__(self):
        self.events: Dict[str, Dict[str, Any]] = {}
        self.prompt_aggregates: Dict[str, Dict[str, Any]] = {}
        self.aggregate_features: Dict[str, Dict[str, str]] = {}
        self.aggregate_members: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.aggregate_links: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        self.warnings: Dict[str, Dict[str, Any]] = {}
        self.outcomes: List[Dict[str, Any]] = []
        self.states: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.manifests: Dict[str, Dict[str, Any]] = {}

    def record_event(self, event: Event) -> bool:
        record = event.public_record()
        _privacy_safe(record)
        if event.event_id in self.events:
            return False
        self.events[event.event_id] = record
        return True

    def record_events(self, events: Iterable[Event]) -> List[bool]:
        """Memory-store equivalent of one atomic bounded offline chunk."""
        prepared: List[Tuple[str, Dict[str, Any]]] = []
        new_ids = set()
        results: List[bool] = []
        for index, event in enumerate(events):
            if index >= MAX_BATCH_EVENTS:
                raise ValueError("offline event batch exceeds %d" % MAX_BATCH_EVENTS)
            record = event.public_record()
            _privacy_safe(record)
            inserted = event.event_id not in self.events and event.event_id not in new_ids
            results.append(inserted)
            if inserted:
                new_ids.add(event.event_id)
                prepared.append((event.event_id, record))
        # Validation and the size guard finish before the first mutation.
        for event_id, record in prepared:
            self.events[event_id] = record
        return results

    def record_prompt_aggregate(self, aggregate: PromptAggregate | Mapping[str, Any]) -> bool:
        return self.record_prompt_aggregates((aggregate,))[0]

    def record_prompt_aggregates(
            self, aggregates: Iterable[PromptAggregate | Mapping[str, Any]]) -> List[bool]:
        """Atomic in-memory equivalent of the compact aggregate transaction."""
        prepared: List[Dict[str, Any]] = []
        batch_payloads: Dict[str, str] = {}
        batch_features: Dict[str, Dict[str, str]] = {}
        results: List[bool] = []
        for index, aggregate in enumerate(aggregates):
            if index >= MAX_BATCH_AGGREGATES:
                raise ValueError("offline aggregate batch exceeds %d" % MAX_BATCH_AGGREGATES)
            record = _prepare_aggregate(aggregate)
            aggregate_id = record["aggregate_id"]
            existing = self.prompt_aggregates.get(aggregate_id)
            known_payload = existing.get("payload_hash") if existing else batch_payloads.get(aggregate_id)
            if known_payload is not None:
                if str(known_payload) != record["payload_hash"]:
                    raise AggregateConflictError("aggregate id already has a different payload")
                results.append(False)
                continue
            for feature in record["features"]:
                feature_id = stable_hash(feature, "aggregate-feature")
                known = self.aggregate_features.get(feature_id) or batch_features.get(feature_id)
                if known and (known["feature"] != feature
                              or known["feature_kind"] != record["feature_kinds"][feature]):
                    raise AggregateConflictError("aggregate feature hash collision")
                batch_features[feature_id] = {
                    "feature": feature, "feature_kind": record["feature_kinds"][feature],
                }
            batch_payloads[aggregate_id] = record["payload_hash"]
            prepared.append(record)
            results.append(True)
        # Every row and cross-row collision is validated before mutation.
        for record in prepared:
            aggregate_id = record["aggregate_id"]
            self.prompt_aggregates[aggregate_id] = dict(record)
            feature_ids = {}
            for feature in record["features"]:
                feature_id = stable_hash(feature, "aggregate-feature")
                feature_ids[feature] = feature_id
                self.aggregate_features.setdefault(
                    feature_id, {"feature": feature, "feature_kind": record["feature_kinds"][feature]},
                )
                self.aggregate_members[(aggregate_id, feature_id)] = {
                    "role": record["feature_kinds"][feature],
                    "occurrence_weight": record["occurrence_count"],
                }
            for src, dst, relation in record["links"]:
                self.aggregate_links[(aggregate_id, feature_ids[src], feature_ids[dst], relation)] = {
                    "occurrence_weight": record["occurrence_count"],
                }
        return results

    def recent_events(self, session_hash: str, limit: int = 32, before: Optional[float] = None,
                      observed_before: Optional[float] = None,
                      before_event_id: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = [row for row in self.events.values() if row["session_hash"] == session_hash
                and (before is None or float(row["event_ts"]) < float(before)
                     or (float(row["event_ts"]) == float(before)
                         and row["event_id"] < str(before_event_id or "\uffff")))
                and (observed_before is None or float(row["observed_ts"]) <= float(observed_before))]
        rows.sort(key=lambda row: (float(row["event_ts"]), row["event_id"]))
        return [dict(row) for row in rows[-max(1, int(limit)):]]

    def record_warning(self, warning: Mapping[str, Any]) -> bool:
        _privacy_safe(warning)
        mode, status = _warning_control(warning)
        row = dict(warning)
        row.update({"mode": mode, "status": status})
        self.warnings.setdefault(str(row["trace_id"]), row)
        return True

    def record_warning_if_fresh(self, warning: Mapping[str, Any], refractory_seconds: float,
                                burden_bump: float = 1.0) -> bool:
        key = (str(warning["session_hash"]), str(warning["family"]))
        state = self.states.get(key)
        trace_id = str(warning["trace_id"])
        if not state or trace_id in self.warnings:
            return False
        created = float(warning.get("created_at") or time.time())
        last_fire = state.get("last_fire_ts")
        if last_fire is not None and created - float(last_fire) < float(refractory_seconds):
            return False
        if not self.record_warning(warning):
            return False
        state["last_fire_ts"] = created
        state["warning_burden"] = min(8.0, float(state.get("warning_burden") or 0.0) + float(burden_bump))
        return True

    def pending_warnings(self, session_hash: str, limit: int = 16) -> List[Dict[str, Any]]:
        rows = []
        for warning in self.warnings.values():
            if warning.get("session_hash") != session_hash or warning.get("status") != "pending":
                continue
            row = dict(warning)
            event = self.events.get(str(row.get("event_id"))) or {}
            row["warning_episode_id"] = event.get("episode_id")
            row["warning_surface"] = event.get("surface")
            row["warning_features"] = list(event.get("features") or ())
            rows.append(row)
        rows.sort(key=lambda row: float(row.get("created_at") or 0), reverse=True)
        return rows[:max(1, int(limit))]

    def candidate_warnings(self, session_hash: str, action_fingerprint: str,
                           limit: int = 16) -> List[Dict[str, Any]]:
        if not action_fingerprint:
            return []
        rows = [dict(row) for row in self.warnings.values()
                if row.get("session_hash") == session_hash
                and row.get("action_fingerprint") == action_fingerprint
                and row.get("status") == "candidate"]
        rows.sort(key=lambda row: float(row.get("created_at") or 0), reverse=True)
        return rows[:max(1, int(limit))]

    def resolve_candidate_with_outcome(self, trace_id: str, event: Event, outcome: str) -> bool:
        if outcome not in {"success", "failure"}:
            raise ValueError("invalid candidate outcome")
        row = self.warnings.get(trace_id)
        if not row or row.get("status") != "candidate" or row.get("session_hash") != event.session_hash:
            return False
        status = "candidate_" + outcome
        row.update({"status": status, "resolved_event_id": event.event_id,
                    "resolution_ts": event.observed_ts})
        return bool(self.record_outcome(
            event, status, severity=0.5 if outcome == "failure" else 0.0,
            warning_trace_id=trace_id, adjudication="prospective_unexposed",
            attrs={"reason_code": "prospective_" + outcome, "exposure": "unexposed"}))

    def resolve_warning(self, trace_id: str, status: str, event_id: str, ts: Optional[float] = None) -> bool:
        row = self.warnings.get(trace_id)
        if not row or row.get("status") != "pending":
            return False
        row.update({"status": status, "resolved_event_id": event_id,
                    "resolution_ts": float(ts or time.time())})
        return True

    def resolve_warning_with_outcome(self, trace_id: str, status: str, event: Event,
                                     reason_code: str) -> bool:
        row = self.warnings.get(trace_id)
        if not row or row.get("status") != "pending" or row.get("session_hash") != event.session_hash:
            return False
        row.update({"status": status, "resolved_event_id": event.event_id,
                    "resolution_ts": event.observed_ts})
        return bool(self.record_outcome(event, "warning_" + status, warning_trace_id=trace_id,
                                        attrs={"reason_code": reason_code}))

    def record_outcome(self, event: Event, kind: str, severity: float = 0.0,
                       warning_trace_id: Optional[str] = None, adjudication: str = "observed",
                       attrs: Optional[Mapping[str, Any]] = None) -> Optional[str]:
        attrs = dict(attrs or {})
        _privacy_safe(attrs)
        outcome_id = stable_hash("|".join((event.event_id, kind, warning_trace_id or "")), "outcome")
        if not any(row["outcome_id"] == outcome_id for row in self.outcomes):
            self.outcomes.append({"outcome_id": outcome_id, "event_id": event.event_id,
                                  "session_hash": event.session_hash, "kind": kind,
                                  "severity": float(severity), "warning_trace_id": warning_trace_id,
                                  "adjudication": adjudication, "created_at": event.observed_ts,
                                  "attrs": attrs})
        return outcome_id

    def get_family_state(self, session_hash: str, family: str) -> Dict[str, Any]:
        return dict(self.states.get((session_hash, family),
                                    {"activation": 0.0, "warning_burden": 0.0, "last_ts": 0.0,
                                     "last_fire_ts": None, "sequence": {}}))

    def put_family_state(self, session_hash: str, family: str, state: Mapping[str, Any]) -> bool:
        self.states[(session_hash, family)] = dict(state)
        return True

    def advance_family_state(self, session_hash: str, family: str, event_ts: float, evidence: float,
                             config: Mapping[str, Any]) -> Dict[str, Any]:
        previous = self.get_family_state(session_hash, family)
        last_ts = float(previous.get("last_ts") or event_ts)
        dt = max(0.0, float(event_ts) - last_ts)
        ahl = max(1.0, float(config.get("activation_half_life", 1200.0)))
        bhl = max(1.0, float(config.get("burden_half_life", 900.0)))
        burden = float(previous.get("warning_burden") or 0.0) * (0.5 ** (dt / bhl))
        activation = float(previous.get("activation") or 0.0) * (0.5 ** (dt / ahl))
        activation += float(evidence) - float(config.get("burden_penalty", 0.22)) * burden
        state = {"activation": max(-8.0, min(8.0, activation)), "warning_burden": max(0.0, burden),
                 "last_ts": max(last_ts, float(event_ts)), "last_fire_ts": previous.get("last_fire_ts"),
                 "sequence": dict(previous.get("sequence") or {}), "_ok": True}
        self.states[(session_hash, family)] = dict(state)
        return state

    def claim_family_fire(self, session_hash: str, family: str, event_ts: float,
                          refractory_seconds: float, burden_bump: float = 1.0) -> bool:
        state = self.states.get((session_hash, family))
        if not state:
            return False
        last_fire = state.get("last_fire_ts")
        if last_fire is not None and float(event_ts) - float(last_fire) < float(refractory_seconds):
            return False
        state["last_fire_ts"] = float(event_ts)
        state["warning_burden"] = min(8.0, float(state.get("warning_burden") or 0.0) + float(burden_bump))
        return True

    def counts(self) -> Dict[str, int]:
        return {"nodes": 0, "episodes": len({row["episode_id"] for row in self.events.values()}),
                "events": len(self.events), "edges": 0,
                "prompt_aggregates": len(self.prompt_aggregates),
                "aggregate_feature_catalog": len(self.aggregate_features),
                "prompt_aggregate_members": len(self.aggregate_members),
                "prompt_aggregate_links": len(self.aggregate_links), "warnings": len(self.warnings),
                "outcomes": len(self.outcomes), "source_manifest": len(self.manifests)}

    def get_source_manifest(self, source_hash: str) -> Optional[Dict[str, Any]]:
        row = self.manifests.get(source_hash)
        return dict(row) if row else None

    def put_source_manifest(self, record: Mapping[str, Any]) -> bool:
        _privacy_safe(record.get("attrs") or {})
        self.manifests[str(record["source_hash"])] = dict(record)
        return True

    def integrity_report(self) -> Dict[str, Any]:
        unsafe = sum(1 for row in self.aggregate_features.values()
                     if row["feature"].startswith("lex:") or "\\" in row["feature"]
                     or len(row["feature"]) > 160)
        return {"integrity": "ok", "rawish_labels": 0,
                "unsafe_aggregate_features": unsafe, "counts": self.counts()}
