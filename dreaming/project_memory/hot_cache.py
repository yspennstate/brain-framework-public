"""Signed, read-only hot-path cache for prompt-contract enforcement.

The hook may be called tens of thousands of times per day.  It must never install a
schema, scan a corpus, or fsync SQLite on the tool path.  The hourly reconciler builds
this compact cache; PreToolUse verifies and reads it only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import base64
from pathlib import Path

from .clauses import feature_digests
from .classify import CLASSIFIER_VERSION, classification_digest
from .drift import DRIFT_THRESHOLD, RESIDUAL_THRESHOLD, operation_for
from .store import AUTHORITATIVE_EVENT_SQL, ProjectMemoryStore, utc_now

CACHE_SCHEMA = "project-contract-hot-cache-v3"
BLOOM_BITS = 131_072
BLOOM_HASHES = 3


def _support_bloom(features: set[str]) -> str:
    bits = bytearray(BLOOM_BITS // 8)
    for feature in features:
        raw = feature.encode("ascii", "strict")
        for salt in range(BLOOM_HASHES):
            index = int.from_bytes(hashlib.sha256(bytes([salt]) + raw).digest()[:8], "big") % BLOOM_BITS
            bits[index // 8] |= 1 << (index % 8)
    return base64.b64encode(bytes(bits)).decode("ascii")


def cache_path(root: Path) -> Path:
    return root / "hot-contract-cache.json"


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-%d" % os.getpid())
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def build_contract_cache(store: ProjectMemoryStore, config: dict | None = None) -> Path:
    config = config or {}
    classification_meta = dict(store.db.execute(
        "SELECT key,value FROM schema_meta WHERE key IN "
        "('classification_config_digest','classification_classifier_version')"
    ).fetchall())
    stored_digest = classification_meta.get("classification_config_digest")
    if stored_digest and stored_digest != classification_digest(config):
        raise RuntimeError("classification_config_changed_requires_explicit_rebuild")
    stored_version = classification_meta.get("classification_classifier_version")
    if stored_version and stored_version != CLASSIFIER_VERSION:
        raise RuntimeError("classification_version_changed_requires_explicit_rebuild")
    inventory = dict(store.db.execute(
        "SELECT key,value FROM schema_meta WHERE key IN ('source_inventory_total','source_inventory_covered')"
    ).fetchall())
    inventory_total = int(inventory.get("source_inventory_total", 0))
    inventory_covered = int(inventory.get("source_inventory_covered", 0))
    backfill_pending = inventory_total > inventory_covered
    contracts: dict[str, list[dict]] = {}
    support_blooms: dict[str, str] = {}
    support_clause_counts: dict[str, int] = {}
    for project_id in store.active_project_ids():
        rows = store.db.execute(
            "SELECT c.clause_id,c.role,c.weight,c.features_json,c.forbidden_ops_json,op.state "
            "FROM clauses c JOIN prompt_events p ON p.event_id=c.event_id "
            "JOIN clause_assignments ca ON ca.clause_id=c.clause_id "
            "JOIN obligation_progress op ON op.clause_id=c.clause_id "
            "WHERE ca.project_id=? AND " + AUTHORITATIVE_EVENT_SQL + " AND c.actionable=1 "
            "AND op.state NOT IN ('satisfied','superseded','non_actionable') "
            "ORDER BY p.occurred_at DESC,p.source_id DESC,p.source_seq DESC,c.ordinal LIMIT 2000",
            (project_id,),
        ).fetchall()
        all_rows = list(rows)
        contracts[project_id] = [
            {"clause_id": row["clause_id"], "role": row["role"], "weight": row["weight"],
             "features": json.loads(row["features_json"]),
             "forbidden_ops": json.loads(row["forbidden_ops_json"]), "state": row["state"]}
            for row in all_rows if row["role"] == "prohibition"
        ]
        support_features = {feature for row in all_rows for feature in json.loads(row["features_json"])
                            if str(feature).startswith("lexh:")}
        support_blooms[project_id] = _support_bloom(support_features)
        support_clause_counts[project_id] = len(all_rows)
    session_projects: dict[str, list[str]] = {}
    for row in store.db.execute(
        "SELECT p.session_hmac,ca.project_id,MAX(p.ingest_seq) AS newest FROM prompt_events p "
        "JOIN clauses c ON c.event_id=p.event_id JOIN clause_assignments ca ON ca.clause_id=c.clause_id "
        "WHERE " + AUTHORITATIVE_EVENT_SQL + " "
        "GROUP BY p.session_hmac,ca.project_id ORDER BY newest DESC LIMIT 20000"
    ):
        session_projects.setdefault(str(row[0]), []).append(str(row[1]))
    reviews = {}
    for project_id in store.active_project_ids():
        row = store.latest_review(project_id)
        if not row:
            continue
        attempt_unresolved = (int(row["attempt_generation"] or 0) >
                              int(row["published_generation"] or 0) or backfill_pending)
        if row["window_id"]:
            reviews[project_id] = {
                "window_id": row["window_id"], "generation": int(row["generation"]),
                "occurrence_count": int(row["occurrence_count"]),
                "packet_sha256": row["packet_sha256"], "packet_path": row["packet_path"],
                "attempt_unresolved": attempt_unresolved,
            }
        elif attempt_unresolved:
            reviews[project_id] = {
                "window_id": None, "generation": int(row["published_generation"] or 0),
                "occurrence_count": 0, "packet_sha256": None, "packet_path": None,
                "attempt_unresolved": True,
            }
    acknowledgements: dict[str, list[str]] = {}
    for row in store.db.execute(
        "SELECT o.session_hmac,rw.project_id,rw.window_id FROM context_acknowledgements a "
        "JOIN context_offers o ON o.offer_id=a.offer_id "
        "JOIN review_windows rw ON rw.window_id=o.window_id"
    ):
        acknowledgements.setdefault(str(row[0]), []).append(str(row[2]))
    drift = {}
    for row in store.db.execute(
        "SELECT session_hmac,project_id,drift,residual,consecutive_low_support FROM drift_state"
    ):
        drift[str(row[0]) + ":" + str(row[1])] = {
            "drift": float(row[2]), "residual": float(row[3]), "low_streak": int(row[4])}
    policy = {
        # These are normalized by ``load_config`` before cache publication.  The
        # standalone hook deliberately does not reopen deployment config: the
        # exact roots and enforcement controls it trusts are part of the signed
        # cache generation.
        "projects": [
            {"id": str(project.get("id")), "roots": list(project.get("roots") or ())}
            for project in config.get("projects") or () if project.get("active", True)
        ],
        "deny_exact_negative": False,
        "instant_drift_threshold": float(config.get("instant_drift_threshold", 0.05)),
        "drift_min_progress_actions": max(1, int(config.get("drift_min_progress_actions", 5))),
        "drift_min_low_streak": max(2, int(config.get("drift_min_low_streak", 3))),
        "drift_warning_cooldown_seconds": max(
            60, int(config.get("drift_warning_cooldown_seconds", 900))
        ),
        "max_cache_age_seconds": max(300, int(config.get("max_cache_age_seconds", 7500))),
    }
    classification = dict(store.db.execute(
        "SELECT key,value FROM schema_meta WHERE key IN "
        "('classification_config_digest','classification_classifier_version',"
        "'classification_last_rebuild_id')"
    ).fetchall())
    body = {"schema": CACHE_SCHEMA, "built_at": utc_now(), "manifest": store.manifest_digest(),
            "contracts": contracts, "session_projects": session_projects,
            "reviews": reviews, "acknowledgements": acknowledgements, "drift": drift,
            "policy": policy, "support_blooms": support_blooms,
            "support_clause_counts": support_clause_counts,
            "classification": classification,
            "bloom": {"bits": BLOOM_BITS, "hashes": BLOOM_HASHES},
            "source_coverage": {"covered": inventory_covered, "total": inventory_total,
                                "backfill_pending": backfill_pending}}
    envelope = {"body": body, "signature": hmac.new(store.key, _canonical(body), hashlib.sha256).hexdigest()}
    target = cache_path(store.root)
    _atomic_bytes(target, _canonical(envelope) + b"\n")
    return target


def load_contract_cache(root: Path, key: bytes) -> dict | None:
    try:
        envelope = json.loads(cache_path(root).read_text(encoding="utf-8"))
        body = envelope["body"]
        expected = hmac.new(key, _canonical(body), hashlib.sha256).hexdigest()
        if body.get("schema") != CACHE_SCHEMA or not hmac.compare_digest(str(envelope["signature"]), expected):
            return None
        return body
    except Exception:
        return None


def _scope_match(keyed, clause_features: set[str], action_features: set[str]) -> bool:
    operation_words = ("delete remove erase write edit change modify overwrite push publish release "
                       "deploy launch start run execute invoke send message email upload download")
    op_features = {"lexh:" + keyed("lexeme", word)[:24] for word in operation_words.split()}
    scope = {item for item in clause_features if item.startswith("lexh:")} - op_features
    return not scope or bool(scope & action_features)


def evaluate_hot(cache: dict, key: bytes, *, session: str, tool_name: str,
                 tool_input: object, cwd: str, config: dict | None = None) -> dict:
    def keyed(domain: str, value: str) -> str:
        material = (domain + "\x1f" + str(value)).encode("utf-8", "replace")
        return hmac.new(key, material, hashlib.sha256).hexdigest()

    session_hmac = keyed("session", session or "unknown")[:32]
    projects = list(cache.get("session_projects", {}).get(session_hmac, ()))
    cwd_norm = os.path.normcase(os.path.abspath(cwd)) if cwd else ""
    policy = cache.get("policy") or {}
    project_rows = policy.get("projects") or (config or {}).get("projects") or ()
    if cwd_norm:
        rooted = []
        for project in project_rows:
            for root in project.get("roots") or []:
                if cwd_norm == root or cwd_norm.startswith(root + os.sep):
                    rooted.append((len(root), project["id"]))
        if rooted:
            deepest = max(length for length, _project in rooted)
            projects = sorted({project for length, project in rooted if length == deepest})
    operation, progress = operation_for(tool_name, tool_input)
    action_text = json.dumps(tool_input, sort_keys=True, ensure_ascii=False) if isinstance(tool_input, dict) else str(tool_input or "")
    features = set(feature_digests(action_text[:40_000], keyed))
    violations = []
    for project_id in projects:
        for clause in cache.get("contracts", {}).get(project_id, ()):
            if (operation in set(clause.get("forbidden_ops") or ()) and (progress or operation == "read")
                    and _scope_match(keyed, set(clause.get("features") or ()), features)):
                violations.append({"project_id": project_id, "clause_id": clause["clause_id"]})
    drifted = []
    for project_id in projects:
        state = cache.get("drift", {}).get(session_hmac + ":" + project_id, {})
        if (float(state.get("drift", 0)) >= DRIFT_THRESHOLD
                and float(state.get("residual", 0)) >= RESIDUAL_THRESHOLD
                and int(state.get("low_streak", 0)) >= 3):
            drifted.append({"project_id": project_id, **state})
    acknowledged = set(cache.get("acknowledgements", {}).get(session_hmac, ()))
    due = [dict(cache["reviews"][project_id], project_id=project_id)
           for project_id in projects
           if project_id in cache.get("reviews", {})
           and cache["reviews"][project_id]["window_id"] not in acknowledged]
    return {"operation": operation, "projects": projects, "violations": violations,
            "drift": drifted, "due_reviews": due}
