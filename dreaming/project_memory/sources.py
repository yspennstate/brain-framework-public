"""Occurrence-level, append-aware prompt source adapters.

Framework code knows formats, never deployment paths or prompt text.  Raw text remains
in memory just long enough to project clauses and build private review packets.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .store import AUTHORITATIVE_EVENT_SQL, ProjectMemoryStore, utc_now


@dataclass(frozen=True)
class PromptOccurrence:
    source_id: str
    source_seq: int
    text: str
    session: str
    occurred_at: str
    runtime: str
    cwd: str
    source_project: str = ""
    provenance: str = "owner"


def _occurrence_record(occurrence: PromptOccurrence) -> dict:
    return {
        "text": occurrence.text,
        "session": occurrence.session,
        "occurred_at": occurrence.occurred_at,
        "runtime": occurrence.runtime,
        "cwd": occurrence.cwd,
        "source_project": occurrence.source_project,
        "provenance": occurrence.provenance,
    }


def _source_id(kind: str, path: Path) -> str:
    """Return the v2 identity for normalized, owner-facing source rows."""
    prefix = "claude-transcript-v2:" if kind == "claude" else "codex-rollout-v2:"
    return prefix + path.stem


def _owner_payload(value: str) -> str:
    """Use exactly the same human-only projection as the live submit hook."""
    parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if parent not in sys.path:
        sys.path.insert(0, parent)
    import prompt_archive
    return prompt_archive.human_part(value)


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
            value = item.get("text")
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def _claude_rows(path: Path, lines: list[bytes]) -> Iterable[PromptOccurrence]:
    source_id = _source_id("claude", path)
    for line_no, raw in enumerate(lines, 1):
        try:
            row = json.loads(raw.decode("utf-8", "strict"))
        except Exception:
            continue
        if (not isinstance(row, dict) or row.get("type") != "user"
                or row.get("isSidechain") is True):
            continue
        message = row.get("message") or {}
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        raw_text = _content_text(message.get("content"))
        text = _owner_payload(raw_text)
        session = str(row.get("sessionId") or row.get("session_id") or path.stem)
        occurred_at = str(row.get("timestamp") or utc_now())
        cwd = str(row.get("cwd") or "")
        source_project = str(row.get("project") or "")
        if text:
            yield PromptOccurrence(
                source_id=source_id, source_seq=line_no, text=text,
                session=session, occurred_at=occurred_at, runtime="claude-transcript",
                cwd=cwd, source_project=source_project,
                provenance="automation" if row.get("isMeta") is True else "owner",
            )
        # A short-lived hook generation fell back to raw Claude wrapper bytes
        # when prompt_archive was unavailable. Retain an inert, source-bound
        # legacy occurrence so those HMAC-only observations can reconcile,
        # while the normalized v2 row remains the sole owner-policy source.
        if raw_text and raw_text != text:
            yield PromptOccurrence(
                source_id="claude-wrapper-v1:" + path.stem,
                source_seq=line_no, text=raw_text,
                session=session, occurred_at=occurred_at, runtime="claude-wrapper-legacy",
                cwd=cwd, source_project=source_project,
                provenance="automation",
            )
        if text and raw_text != text and raw_text != text + "\n":
            yield PromptOccurrence(
                source_id="claude-human-v1:" + path.stem,
                source_seq=line_no, text=text + "\n",
                session=session, occurred_at=occurred_at, runtime="claude-human-legacy",
                cwd=cwd, source_project=source_project, provenance="automation",
            )


def _codex_rows(path: Path, lines: list[bytes]) -> Iterable[PromptOccurrence]:
    source_id = _source_id("codex", path)
    session = path.stem
    cwd = ""
    forked = False
    decoded: list[tuple[int, dict]] = []
    for line_no, raw in enumerate(lines, 1):
        try:
            row = json.loads(raw.decode("utf-8", "strict"))
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        decoded.append((line_no, row))
        if row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
            payload = row["payload"]
            session = str(payload.get("session_id") or payload.get("id") or session)
            cwd = str(payload.get("cwd") or cwd)
            forked = bool(payload.get("forked_from_id") or payload.get("parent_thread_id"))
    if forked:
        return
    has_event_user = any(row.get("type") == "event_msg" and isinstance(row.get("payload"), dict)
                         and row["payload"].get("type") == "user_message" for _line, row in decoded)
    for line_no, row in decoded:
        payload = row.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        text = ""
        if row.get("type") == "event_msg" and payload.get("type") == "user_message":
            text = str(payload.get("message") or "")
        elif (not has_event_user and row.get("type") == "response_item" and payload.get("type") == "message"
              and payload.get("role") == "user"):
            text = _content_text(payload.get("content"))
        if not text:
            continue
        text = _owner_payload(text)
        if not text:
            continue
        yield PromptOccurrence(source_id=source_id, source_seq=line_no, text=text, session=session,
                               occurred_at=str(row.get("timestamp") or utc_now()), runtime="codex-rollout",
                               cwd=cwd)


def _codex_replay_rows(path: Path, lines: list[bytes],
                       sequences: set[int]) -> Iterable[PromptOccurrence]:
    """Replay exact historical locators even after a rollout representation changes.

    Early in a Codex rollout, a user prompt can exist only as a
    ``response_item``.  The same append-only file can later gain ``event_msg``
    rows, which makes the normal ingestion projection prefer that newer
    representation.  Previously ingested source locators must remain
    replayable, so this path reads only the requested line numbers and accepts
    either owner-facing representation at that exact locator.
    """
    source_id = _source_id("codex", path)
    session = path.stem
    cwd = ""
    forked = False
    decoded: dict[int, dict] = {}
    for line_no, raw in enumerate(lines, 1):
        try:
            row = json.loads(raw.decode("utf-8", "strict"))
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if line_no in sequences:
            decoded[line_no] = row
        if row.get("type") == "session_meta" and isinstance(row.get("payload"), dict):
            payload = row["payload"]
            session = str(payload.get("session_id") or payload.get("id") or session)
            cwd = str(payload.get("cwd") or cwd)
            forked = bool(payload.get("forked_from_id") or payload.get("parent_thread_id"))
    if forked:
        return
    for line_no in sorted(decoded):
        row = decoded[line_no]
        payload = row.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        text = ""
        if row.get("type") == "event_msg" and payload.get("type") == "user_message":
            text = str(payload.get("message") or "")
        elif (row.get("type") == "response_item" and payload.get("type") == "message"
              and payload.get("role") == "user"):
            text = _content_text(payload.get("content"))
        text = _owner_payload(text)
        if text:
            yield PromptOccurrence(
                source_id=source_id, source_seq=line_no, text=text, session=session,
                occurred_at=str(row.get("timestamp") or utc_now()), runtime="codex-rollout",
                cwd=cwd,
            )


def _complete_lines(path: Path) -> tuple[bytes, list[bytes], os.stat_result]:
    data = path.read_bytes()
    complete = data.rfind(b"\n") + 1
    payload = data[:complete]
    return payload, payload.splitlines(), path.stat()


def _validated_json_lines(lines: list[bytes], source_id: str) -> list[bytes]:
    """Validate complete JSONL records, repairing only a known sparse-hole prefix.

    A Codex crash can leave NUL bytes before an otherwise complete JSON object.
    The raw manifest remains bound to the original bytes; only the in-memory
    parser view drops an all-NUL prefix.  Every other malformed record fails
    closed so corruption cannot silently omit an owner prompt.
    """
    validated = []
    for line_no, raw in enumerate(lines, 1):
        candidate = raw
        try:
            json.loads(candidate.decode("utf-8", "strict"))
        except Exception as original:
            candidate = raw.lstrip(b"\x00")
            if candidate == raw or not candidate.startswith(b"{"):
                raise RuntimeError(
                    "source_malformed_complete_json:%s:%d" % (source_id, line_no)
                ) from original
            try:
                json.loads(candidate.decode("utf-8", "strict"))
            except Exception as repaired:
                raise RuntimeError(
                    "source_malformed_complete_json:%s:%d" % (source_id, line_no)
                ) from repaired
        validated.append(candidate)
    return validated


def _is_workflow_journal(kind: str, path: Path) -> bool:
    """Match only Claude workflow journals, never sibling agent transcripts."""
    if kind != "claude" or len(path.parts) < 4:
        return False
    subagents, workflows, workflow, filename = (part.casefold() for part in path.parts[-4:])
    return (subagents == "subagents" and workflows == "workflows"
            and workflow.startswith("wf_") and len(workflow) > 3
            and filename == "journal.jsonl")


def _is_claude_subagent(path: Path) -> bool:
    return "subagents" in {part.casefold() for part in path.parts}


def _codex_owner_facing(path: Path) -> bool:
    """Accept only direct UI/CLI sessions; fail closed on unknown metadata."""
    try:
        with open(path, "rb") as handle:
            raw = handle.readline()
        row = json.loads(raw.decode("utf-8", "strict"))
    except Exception as exc:
        raise RuntimeError("codex_session_meta_unreadable") from exc
    payload = row.get("payload") if isinstance(row, dict) else None
    if (not isinstance(row, dict) or row.get("type") != "session_meta"
            or not isinstance(payload, dict)):
        raise RuntimeError("codex_session_meta_missing")
    if payload.get("forked_from_id") or payload.get("parent_thread_id"):
        return False
    originator = str(payload.get("originator") or "")
    source = payload.get("source")
    if payload.get("thread_source") == "subagent" or (
            isinstance(source, dict) and "subagent" in source):
        return False
    if originator == "codex-tui" and source == "cli":
        return True
    if originator == "Codex Desktop" and source == "vscode":
        return True
    if originator == "codex_exec" and source == "exec":
        return False
    raise RuntimeError("codex_session_provenance_unknown")


def _validate_workflow_journal(path: Path) -> None:
    """Fail closed if a journal begins carrying user prompts or an unknown schema."""
    _payload, lines, _stat = _complete_lines(path)
    for raw in lines:
        try:
            row = json.loads(raw.decode("utf-8", "strict"))
        except Exception as exc:
            raise RuntimeError("workflow_journal_schema_changed") from exc
        if not isinstance(row, dict) or row.get("type") not in {"started", "result"}:
            raise RuntimeError("workflow_journal_schema_changed")
        allowed_keys = ({"type", "key", "agentId"} if row["type"] == "started" else
                        {"type", "key", "agentId", "result"})
        if set(row) != allowed_keys:
            raise RuntimeError("workflow_journal_schema_changed")
        if not isinstance(row.get("key"), str) or not row["key"].strip():
            raise RuntimeError("workflow_journal_schema_changed")
        if not isinstance(row.get("agentId"), str) or not row["agentId"].strip():
            raise RuntimeError("workflow_journal_schema_changed")
        if row["type"] == "result" and "result" not in row:
            raise RuntimeError("workflow_journal_schema_changed")


def _validate_manifest(store: ProjectMemoryStore, source_id: str, payload: bytes,
                       line_count: int, stat: os.stat_result) -> dict:
    prior = store.db.execute("SELECT * FROM source_manifests WHERE source_id=?", (source_id,)).fetchone()
    if prior:
        old_size = int(prior["size_bytes"])
        if len(payload) < old_size:
            raise RuntimeError("source_truncated:" + source_id)
        old_digest = hashlib.sha256(payload[:old_size]).hexdigest()
        if store.keyed("source-prefix", old_digest) != str(prior["prefix_hmac"]):
            raise RuntimeError("source_prefix_changed:" + source_id)
        if (len(payload) == old_size and int(getattr(stat, "st_mtime_ns", 0)) == int(prior["mtime_ns"])):
            return {}
    digest = hashlib.sha256(payload).hexdigest()
    return {"source_id": source_id, "size_bytes": len(payload), "line_count": line_count,
            "prefix_hmac": store.keyed("source-prefix", digest), "cursor_seq": line_count,
            "mtime_ns": int(getattr(stat, "st_mtime_ns", 0)), "observed_at": utc_now()}


def commit_manifests(store: ProjectMemoryStore, pending: list[dict]) -> None:
    """Advance cursors only after every occurrence in the batch is durable."""
    with store.immediate():
        for row in pending:
            store.db.execute(
                "INSERT INTO source_manifests(source_id,size_bytes,line_count,prefix_hmac,cursor_seq,mtime_ns,observed_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET size_bytes=excluded.size_bytes,"
                "line_count=excluded.line_count,prefix_hmac=excluded.prefix_hmac,cursor_seq=excluded.cursor_seq,"
                "mtime_ns=excluded.mtime_ns,observed_at=excluded.observed_at",
                (row["source_id"], row["size_bytes"], row["line_count"], row["prefix_hmac"],
                 row["cursor_seq"], row["mtime_ns"], row["observed_at"]),
            )


def configured_files(config: dict) -> list[tuple[str, Path]]:
    out = []
    for spec in config.get("sources") or []:
        if not isinstance(spec, dict) or spec.get("type") not in {"claude", "codex"}:
            continue
        root = Path(os.path.expandvars(os.path.expanduser(str(spec.get("root") or "")))).resolve()
        pattern = str(spec.get("glob") or "*.jsonl")
        if not root.is_dir():
            continue
        for path in root.glob(pattern):
            if path.is_file() and path.name != "PROMPTS.jsonl":
                kind = str(spec["type"])
                resolved = path.resolve()
                if _is_workflow_journal(kind, resolved):
                    _validate_workflow_journal(resolved)
                    continue
                if kind == "claude" and _is_claude_subagent(resolved):
                    continue
                if kind == "codex" and not _codex_owner_facing(resolved):
                    continue
                out.append((kind, resolved))
    return sorted(set(out), key=lambda item: (-item[1].stat().st_mtime_ns, item[0], str(item[1]).casefold()))


def _configured_replay_files(config: dict, needed_source_ids: set[str]) -> dict[str, tuple[str, Path]]:
    """Resolve replay-only archives without adding them to the ingestion inventory."""
    out: dict[str, tuple[str, Path]] = {}
    if not needed_source_ids:
        return out
    for spec in config.get("replay_sources") or []:
        if not isinstance(spec, dict) or spec.get("type") not in {"claude", "codex"}:
            continue
        kind = str(spec["type"])
        root = Path(os.path.expandvars(os.path.expanduser(str(spec.get("root") or "")))).resolve()
        pattern = str(spec.get("glob") or "*.jsonl")
        if not root.is_dir():
            continue
        for path in root.glob(pattern):
            # A replay root can contain thousands of durable transcripts.  The
            # requested source identities already bind exact basenames, so do
            # not stat, resolve, or inspect any unrelated archive member.
            candidate_source_id = _source_id(kind, path)
            if candidate_source_id not in needed_source_ids:
                continue
            if not path.is_file() or path.name == "PROMPTS.jsonl":
                continue
            resolved = path.resolve()
            source_id = _source_id(kind, resolved)
            if source_id != candidate_source_id:
                raise RuntimeError("source_replay_identity_changed:" + candidate_source_id)
            if _is_workflow_journal(kind, resolved):
                _validate_workflow_journal(resolved)
                continue
            if kind == "claude" and _is_claude_subagent(resolved):
                continue
            if kind == "codex" and not _codex_owner_facing(resolved):
                continue
            descriptor = (kind, resolved)
            prior = out.get(source_id)
            if prior is not None and prior != descriptor:
                raise RuntimeError("source_replay_collision:" + source_id)
            out[source_id] = descriptor
    return out


def _validate_replay_prefix(store: ProjectMemoryStore, source_id: str, payload: bytes) -> None:
    """Bind a rotated archive to the exact source prefix previously observed live."""
    prior = store.db.execute(
        "SELECT size_bytes,prefix_hmac FROM source_manifests WHERE source_id=?", (source_id,)
    ).fetchone()
    if not prior:
        raise RuntimeError("source_replay_manifest_missing:" + source_id)
    observed_size = int(prior["size_bytes"])
    if len(payload) < observed_size:
        raise RuntimeError("source_replay_truncated:" + source_id)
    digest = hashlib.sha256(payload[:observed_size]).hexdigest()
    if not hmac.compare_digest(store.keyed("source-prefix", digest), str(prior["prefix_hmac"])):
        raise RuntimeError("source_replay_prefix_changed:" + source_id)


def _hydrate_source_sequences(kind: str, path: Path, source_id: str,
                              sequences: set[int], *, store: ProjectMemoryStore,
                              replay_only: bool) -> dict[tuple[str, int], dict]:
    payload, lines, _stat = _complete_lines(path)
    lines = _validated_json_lines(lines, source_id)
    if replay_only:
        _validate_replay_prefix(store, source_id, payload)
    parser = _claude_rows if kind == "claude" else (
        lambda selected_path, selected_lines:
        _codex_replay_rows(selected_path, selected_lines, sequences)
    )
    hydrated: dict[tuple[str, int], dict] = {}
    for occurrence in parser(path, lines):
        if occurrence.source_seq in sequences and occurrence.source_id == source_id:
            key = (source_id, occurrence.source_seq)
            record = _occurrence_record(occurrence)
            prior = hydrated.get(key)
            if prior is not None and prior != record:
                raise RuntimeError("source_locator_ambiguous:%s:%d" % key)
            hydrated[key] = record
    return hydrated


def source_descriptors(store: ProjectMemoryStore, config: dict,
                       needed_source_ids: set[str], *,
                       require_all: bool = True
                       ) -> dict[str, tuple[str, Path, bool]]:
    """Resolve every requested source identity to exactly one current or replay file."""
    if not needed_source_ids:
        return {}
    current: dict[str, tuple[str, Path, bool]] = {}
    for kind, path in configured_files(config):
        source_id = _source_id(kind, path)
        descriptor = (kind, path, False)
        prior = current.get(source_id)
        if prior is not None and prior != descriptor:
            raise RuntimeError("source_id_collision:" + source_id)
        current[source_id] = descriptor
    missing = set(needed_source_ids).difference(current)
    replay = _configured_replay_files(config, missing)
    for source_id, (kind, path) in replay.items():
        if source_id in current:
            raise RuntimeError("source_current_replay_collision:" + source_id)
        current[source_id] = (kind, path, True)
    unresolved = sorted(set(needed_source_ids).difference(current))
    if unresolved and require_all:
        raise RuntimeError("authoritative_source_unavailable:" + ",".join(unresolved[:8]))
    return {
        source_id: current[source_id]
        for source_id in sorted(needed_source_ids)
        if source_id in current
    }


def hydrate_event_rows(store: ProjectMemoryStore, config: dict, event_rows: list,
                       *, descriptors: dict[str, tuple[str, Path, bool]] | None = None
                       ) -> dict[tuple[str, int], dict]:
    """Hydrate exact raw occurrences for a bounded set of authoritative DB rows."""
    by_source: dict[str, set[int]] = {}
    for row in event_rows:
        by_source.setdefault(str(row["source_id"]), set()).add(int(row["source_seq"]))
    resolved = descriptors or source_descriptors(store, config, set(by_source))
    hydrated: dict[tuple[str, int], dict] = {}
    for source_id in sorted(by_source):
        descriptor = resolved.get(source_id)
        if descriptor is None:
            raise RuntimeError("authoritative_source_unavailable:" + source_id)
        kind, path, replay_only = descriptor
        hydrated.update(_hydrate_source_sequences(
            kind, path, source_id, by_source[source_id],
            store=store, replay_only=replay_only,
        ))
    expected = {(str(row["source_id"]), int(row["source_seq"])) for row in event_rows}
    missing = sorted(expected.difference(hydrated))
    if missing:
        source_id, source_seq = missing[0]
        raise RuntimeError("authoritative_source_locator_missing:%s:%d" %
                           (source_id, source_seq))
    return hydrated


def scan_sources(store: ProjectMemoryStore, config: dict) -> tuple[list[PromptOccurrence], dict[tuple[str, int], dict], list[dict]]:
    occurrences: list[PromptOccurrence] = []
    source_rows: dict[tuple[str, int], dict] = {}
    pending_manifests: list[dict] = []
    files = configured_files(config)
    path_by_source: dict[str, tuple[str, Path]] = {}
    for kind, path in files:
        source_id = _source_id(kind, path)
        descriptor = (kind, path)
        prior_descriptor = path_by_source.get(source_id)
        if prior_descriptor is not None and prior_descriptor != descriptor:
            raise RuntimeError("source_id_collision:" + source_id)
        path_by_source[source_id] = descriptor
    store.last_source_inventory_total = len(files)
    store.last_source_inventory_ids = frozenset(path_by_source)
    budget = max(1_000_000, int(config.get("source_ingest_budget_bytes", 256_000_000)))
    spent = 0
    for kind, path in files:
        source_id = _source_id(kind, path)
        prior = store.db.execute(
            "SELECT size_bytes,mtime_ns FROM source_manifests WHERE source_id=?", (source_id,)
        ).fetchone()
        stat = path.stat()
        unchanged = (prior and int(prior["size_bytes"]) == int(stat.st_size)
                     and int(prior["mtime_ns"]) == int(stat.st_mtime_ns))
        if unchanged:
            continue
        if spent and spent + stat.st_size > budget:
            continue
        payload, lines, stat = _complete_lines(path)
        lines = _validated_json_lines(lines, source_id)
        pending = _validate_manifest(store, source_id, payload, len(lines), stat)
        if pending:
            pending_manifests.append(pending)
        spent += len(payload)
        parser = _claude_rows if kind == "claude" else _codex_rows
        for occurrence in parser(path, lines):
            occurrences.append(occurrence)
            source_rows[(occurrence.source_id, occurrence.source_seq)] = _occurrence_record(occurrence)
    # Hydrate exact bytes for the latest per-project review occurrences without
    # reparsing the full corpus. This is independent of the backward-ingest budget.
    needed = store.db.execute(
        "SELECT source_id,source_seq FROM (SELECT p.source_id,p.source_seq,ep.project_id,"
        "ROW_NUMBER() OVER(PARTITION BY ep.project_id ORDER BY julianday(p.occurred_at) DESC,"
        "p.source_id DESC,p.source_seq DESC) AS rank "
        "FROM prompt_events p JOIN (SELECT DISTINCT c.event_id,ca.project_id FROM clauses c "
        "JOIN clause_assignments ca ON ca.clause_id=c.clause_id) ep ON ep.event_id=p.event_id "
        "WHERE " + AUTHORITATIVE_EVENT_SQL + ") "
        "WHERE rank<=1000"
    ).fetchall()
    by_source: dict[str, set[int]] = {}
    for row in needed:
        key = (str(row[0]), int(row[1]))
        if key not in source_rows:
            by_source.setdefault(key[0], set()).add(key[1])
    replay_by_source = _configured_replay_files(
        config, set(by_source).difference(path_by_source)
    )
    for source_id, sequences in by_source.items():
        descriptor = path_by_source.get(source_id)
        replay_only = False
        if not descriptor:
            descriptor = replay_by_source.get(source_id)
            replay_only = descriptor is not None
        if descriptor is None:
            continue
        kind, path = descriptor
        source_rows.update(_hydrate_source_sequences(
            kind, path, source_id, sequences, store=store, replay_only=replay_only
        ))
    occurrences.sort(key=lambda item: (item.occurred_at, item.source_id, item.source_seq))
    return occurrences, source_rows, pending_manifests
