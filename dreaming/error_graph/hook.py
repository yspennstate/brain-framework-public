#!/usr/bin/env python3
"""Fail-open hook/CLI adapter for every prospective-error event surface."""
from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import re
import sys
import time
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

try:
    from .engine import ErrorGraphEngine, EngineResult
    from .events import (Event, contains_secret, extract_event, stable_hash,
                         transient_operation_features)
    from .metrics import MetricsStore
    from .store import GraphStore, MemoryStore
except ImportError:
    from engine import ErrorGraphEngine, EngineResult
    from events import (Event, contains_secret, extract_event, stable_hash,
                        transient_operation_features)
    from metrics import MetricsStore
    from store import GraphStore, MemoryStore

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.environ.get("ERROR_GRAPH_RUNTIME_DIR", os.path.expanduser(r"~/.claude/dreaming/error_graph_runtime"))
METRICS_PATH = os.environ.get("ERROR_GRAPH_METRICS_PATH", os.path.join(RUNTIME_DIR, "hook_health.sqlite3"))
# The host accepts a much larger JSON envelope so structured metadata survives,
# but regex/AST extraction sees only a bounded textual projection.  A 32,768
# Unicode-character cap keeps worst-case tool bodies from monopolizing an
# interactive hook while structured paths and action IDs survive separately.
MAX_TOOL_TEXT = 32 * 1024
MAX_HOOK_PAYLOAD_BYTES = 512 * 1024
MAX_TOOL_VALUE_TEXT = 8 * 1024
MAX_TOOL_NODES = 2048
MAX_TOOL_PARTS = 128
MAX_TOOL_CONTAINER_ITEMS = 128
MAX_TRANSCRIPT_TAIL_BYTES = 512 * 1024
MAX_TRANSIENT_SCAN_NODES = MAX_HOOK_PAYLOAD_BYTES
_UNSET = object()
_TOOL_TEXT_PRIORITY = {
    name: rank for rank, name in enumerate((
        "command", "cmd", "script", "shell", "powershell", "code",
        "patch", "diff", "query", "statement", "expression", "arguments",
        "args", "input", "text", "content", "payload", "request",
    ))
}
_TOOL_TEXT_SKIP = frozenset(("image", "bytes", "base64", "screenshot"))
_METRIC_SURFACES = {
    "UserPromptSubmit": "prompt",
    "PreToolUse": "pre_action",
    "PostToolUse": "post_action",
    "Stop": "report",
}
_METRIC_RUNTIMES = frozenset(("claude", "codex", "cli"))


class _TextProjection:
    """O(1)-budget accounting for one transient textual projection."""

    __slots__ = ("parts", "remaining", "nodes", "truncated")

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.remaining = MAX_TOOL_TEXT
        self.nodes = 0
        self.truncated = False

    def add(self, value: str) -> None:
        if not value:
            return
        separator = 1 if self.parts else 0
        available = self.remaining - separator
        if available <= 0 or len(self.parts) >= MAX_TOOL_PARTS:
            self.truncated = True
            return
        limit = min(available, MAX_TOOL_VALUE_TEXT)
        sampled, truncated = _sample_text(value, limit)
        if not sampled:
            self.truncated = self.truncated or bool(value)
            return
        self.parts.append(sampled)
        self.remaining -= separator + len(sampled)
        self.truncated = self.truncated or truncated


def _sample_text(value: str, limit: int) -> Tuple[str, bool]:
    """Keep deterministic head and tail evidence inside an exact char cap."""
    limit = max(0, int(limit))
    if len(value) <= limit:
        return value, False
    if limit == 0:
        return "", True
    if limit == 1:
        return value[-1:], True
    head = (limit - 1) // 2
    tail = limit - head - 1
    return value[:head] + "\n" + value[-tail:], True


def _mapping_items(value: Mapping[Any, Any]) -> Tuple[list[Tuple[Any, Any]], bool]:
    """Prioritize risk-bearing fields, then a deterministic bounded key set."""
    # Host tool schemas use these exact lowercase keys. Direct lookup makes
    # every canonical field impossible to crowd out with whitespace/case
    # aliases and keeps priority collection O(the fixed allowlist), not O(N).
    priority = [(name, value[name]) for name in _TOOL_TEXT_PRIORITY if name in value]
    remaining = max(0, MAX_TOOL_CONTAINER_ITEMS - len(priority))
    ordinary = heapq.nsmallest(
        remaining,
        ((str(key).strip().lower(), str(key), key, item)
         for key, item in value.items()
         if str(key).strip().lower() not in _TOOL_TEXT_SKIP
         and key not in _TOOL_TEXT_PRIORITY),
        key=lambda row: row[:2],
    )
    selected = priority[:MAX_TOOL_CONTAINER_ITEMS]
    selected.extend((row[2], row[3]) for row in ordinary)
    eligible = sum(1 for key in value
                   if str(key).strip().lower() not in _TOOL_TEXT_SKIP)
    return selected, eligible > len(selected)


def _text_values(value: Any, projection: _TextProjection, depth: int = 0) -> None:
    if projection.remaining <= 0 or len(projection.parts) >= MAX_TOOL_PARTS:
        projection.truncated = True
        return
    if projection.nodes >= MAX_TOOL_NODES:
        projection.truncated = True
        return
    projection.nodes += 1
    if depth > 4:
        projection.truncated = True
        return
    if isinstance(value, str):
        projection.add(value)
    elif isinstance(value, Mapping):
        items, truncated = _mapping_items(value)
        projection.truncated = projection.truncated or truncated
        for _key, item in items:
            _text_values(item, projection, depth + 1)
            if projection.remaining <= 0 or projection.nodes >= MAX_TOOL_NODES:
                projection.truncated = True
                break
    elif isinstance(value, (list, tuple)):
        count = min(len(value), MAX_TOOL_CONTAINER_ITEMS)
        selected = []
        for index in range((count + 1) // 2):
            selected.append(value[index])
            if len(selected) < count:
                selected.append(value[-1 - index])
        projection.truncated = projection.truncated or len(value) > count
        for item in selected:
            _text_values(item, projection, depth + 1)
            if projection.remaining <= 0 or projection.nodes >= MAX_TOOL_NODES:
                projection.truncated = True
                break


def project_tool_text(tool_input: Any) -> Tuple[str, bool]:
    projection = _TextProjection()
    _text_values(tool_input, projection)
    return "\n".join(projection.parts), projection.truncated


def _transient_tree_features(value: Any, *, tool_name: str, event_name: str,
                             outcome_hint: str) -> set[str]:
    """Linearly scan public sentinels across one bounded JSON subtree."""
    features: set[str] = set()
    stack = [value]
    seen = set()
    nodes = 0
    characters = 0
    while stack and nodes < MAX_TRANSIENT_SCAN_NODES \
            and characters < MAX_HOOK_PAYLOAD_BYTES:
        current = stack.pop()
        nodes += 1
        if isinstance(current, str):
            remaining = MAX_HOOK_PAYLOAD_BYTES - characters
            sample = current[:remaining]
            characters += len(sample)
            features.update(transient_operation_features(
                sample, tool_name=tool_name, event_name=event_name,
                outcome_hint=outcome_hint))
            continue
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            values = [item for key, item in current.items()
                      if str(key).strip().lower() not in _TOOL_TEXT_SKIP]
            stack.extend(reversed(values))
        elif isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in seen:
                continue
            seen.add(identity)
            stack.extend(reversed(current))
    return features


def project_tool_event(tool_input: Any, *, tool_name: str = "",
                       event_name: str = "event",
                       outcome_hint: str = "") -> Tuple[str, bool, Tuple[str, ...]]:
    """Return the bounded text plus public full-field sentinel features."""
    text, truncated = project_tool_text(tool_input)
    features: set[str] = set()
    if isinstance(tool_input, Mapping):
        roots = [tool_input[key] for key in _TOOL_TEXT_PRIORITY if key in tool_input]
    else:
        roots = [tool_input]
    for root in roots:
        features.update(_transient_tree_features(
            root, tool_name=tool_name, event_name=event_name,
            outcome_hint=outcome_hint))
    return text, truncated, tuple(sorted(features))


def tool_text(tool_input: Any) -> str:
    return project_tool_text(tool_input)[0]


def project_text(value: Any) -> Tuple[str, bool]:
    """Bound prompt/report/response text with the same head-tail policy."""
    if value is None:
        return "", False
    if not isinstance(value, str):
        return project_tool_text(value)
    return _sample_text(value, MAX_TOOL_TEXT)


def _assistant_text(obj: Mapping[str, Any]) -> str:
    message = obj.get("message") if isinstance(obj.get("message"), Mapping) else obj
    role = message.get("role") or obj.get("role") or obj.get("type")
    if role != "assistant":
        return ""
    content = message.get("content") or obj.get("content") or ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(item.get("text") or "") for item in content
                        if isinstance(item, Mapping) and item.get("type") in {"text", "output_text"})
    return ""


def _safe_transcript_path(path: str, allowed_roots: Optional[Sequence[str]] = None,
                          expected_session: Optional[str] = None) -> str:
    if not path or path.startswith(("\\\\", "\\\\?\\", "\\\\.\\")):
        return ""
    roots = tuple((
        os.path.expanduser(r"~/.claude/projects"),
        os.path.expanduser(r"~/.codex/sessions"),
    ) if allowed_roots is None else allowed_roots)
    try:
        resolved = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
        if not os.path.isfile(resolved) or not resolved.lower().endswith(".jsonl"):
            return ""
        session = str(expected_session or "").strip().lower()
        if session and session != "unknown":
            if (not re.fullmatch(r"[a-z0-9_.-]{3,128}", session)
                    or session not in os.path.basename(resolved).lower()):
                return ""
        for root in roots:
            safe_root = os.path.realpath(os.path.abspath(os.path.expanduser(root)))
            if os.path.commonpath((resolved, safe_root)) == safe_root:
                return resolved
    except (OSError, ValueError):
        return ""
    return ""


def last_assistant_text(path: str, max_bytes: int = MAX_TRANSCRIPT_TAIL_BYTES,
                        *, allowed_roots: Optional[Sequence[str]] = None,
                        expected_session: Optional[str] = None) -> str:
    """Read a bounded transcript tail and return the last assistant prose."""
    path = _safe_transcript_path(path, allowed_roots, expected_session)
    if not path:
        return ""
    try:
        limit = max(1, min(int(max_bytes), MAX_TRANSCRIPT_TAIL_BYTES))
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            start = max(0, size - limit)
            handle.seek(start)
            data = handle.read(min(limit, max(0, size - start))).decode("utf-8", "replace")
        lines = data.splitlines()
        if start and lines:
            lines = lines[1:]  # discard a partial first JSONL record
        last = ""
        for line in lines[-2000:]:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            text = _assistant_text(obj).strip()
            if text:
                last = text
        return last
    except Exception:
        return ""


def mode_from_gates(explicit: Optional[str] = None) -> str:
    if explicit in {"off", "shadow", "advisory", "block"}:
        return str(explicit)
    env = os.environ.get("ERROR_GRAPH_MODE")
    if env in {"off", "shadow", "advisory", "block"}:
        return env
    if os.path.exists(os.path.join(RUNTIME_DIR, "BLOCK")):
        return "block"
    if os.path.exists(os.path.join(RUNTIME_DIR, "ADVISORY")):
        return "advisory"
    return "shadow"


def _structured_outcome(payload: Mapping[str, Any], response: Any) -> str:
    """Prefer tool metadata over parsing prose that may merely quote an error."""
    candidates = [response, payload]
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("is_error") is True or candidate.get("success") is False:
            return "failure"
        code = candidate.get("exit_code", candidate.get("exitCode"))
        integral_code: Optional[int] = None
        if isinstance(code, int) and not isinstance(code, bool):
            integral_code = code
        elif isinstance(code, float) and math.isfinite(code) and code.is_integer():
            integral_code = int(code)
        if integral_code is not None and integral_code != 0:
            return "failure"
        status = str(candidate.get("status") or "").lower()
        if status in {"error", "failed", "failure", "denied"}:
            return "failure"
        if candidate.get("is_error") is False or candidate.get("success") is True \
                or integral_code == 0 \
                or status in {"ok", "success", "succeeded", "completed"}:
            return "success"
    return ""


def _runtime_actor(payload: Mapping[str, Any]) -> str:
    """Attribute native Claude and Codex hooks without persisting raw identity."""
    explicit = payload.get("agent_id") or payload.get("runtime")
    if explicit:
        return str(explicit)
    configured = str(os.environ.get("ERROR_GRAPH_RUNTIME")
                     or os.environ.get("ERROR_GRAPH_RUNTIME_HINT") or "").strip().lower()
    if re.fullmatch(r"[a-z][a-z0-9_.-]{0,31}", configured):
        return configured
    # ``last_assistant_message`` is a native Codex Stop field.  Other Codex
    # surfaces include both a turn id and model, while Claude supplies an
    # agent id when it needs attribution finer than the runtime default.
    if "last_assistant_message" in payload or (payload.get("turn_id") and payload.get("model")):
        return "codex"
    return "claude"


def event_from_payload(payload: Mapping[str, Any]) -> Optional[Event]:
    event_name = str(payload.get("hook_event_name") or payload.get("event_name") or "event")
    if event_name == "Stop" and payload.get("stop_hook_active"):
        return None
    tool_input = payload.get("tool_input") or {}
    tool_name = str(payload.get("tool_name") or payload.get("tool") or "")
    response = payload.get("tool_response") or payload.get("response") or ""
    outcome_hint = _structured_outcome(payload, response)
    session = str(payload.get("session_id") or payload.get("session") or "unknown")
    projection_truncated = False
    extra_features: set[str] = set()
    if event_name == "UserPromptSubmit":
        raw_text = payload.get("prompt") or payload.get("text") or ""
        text, projection_truncated = project_text(raw_text)
        extra_features.update(_transient_tree_features(
            raw_text, tool_name=tool_name, event_name=event_name,
            outcome_hint=outcome_hint))
    elif event_name == "Stop":
        raw_text = (payload.get("text") or payload.get("last_assistant_message")
                    or last_assistant_text(str(payload.get("transcript_path") or ""),
                                           expected_session=session))
        text, projection_truncated = project_text(raw_text)
        extra_features.update(_transient_tree_features(
            raw_text, tool_name=tool_name, event_name=event_name,
            outcome_hint=outcome_hint))
    else:
        explicit = payload.get("text")
        tool_projection, tool_truncated, tool_features = project_tool_event(
            tool_input, tool_name=tool_name, event_name=event_name,
            outcome_hint=outcome_hint)
        extra_features.update(tool_features)
        if explicit:
            explicit_projection, explicit_truncated = project_text(explicit)
            combined = tool_projection + ("\n" if tool_projection and explicit_projection else "") \
                + explicit_projection
            text, merge_truncated = _sample_text(combined, MAX_TOOL_TEXT)
            projection_truncated = tool_truncated or explicit_truncated or merge_truncated
            extra_features.update(_transient_tree_features(
                explicit, tool_name=tool_name, event_name=event_name,
                outcome_hint=outcome_hint))
        else:
            text, projection_truncated = tool_projection, tool_truncated
    response_text, response_truncated = project_text(response)
    projection_truncated = projection_truncated or response_truncated
    if contains_secret(response):
        extra_features.add("privacy:redacted")
    targets = []
    if isinstance(tool_input, Mapping):
        for key in ("file_path", "path", "cwd", "workdir", "target"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                targets.append(value)
    source_ref = "%s|%s|%s" % (payload.get("transcript_path") or "", event_name,
                                 payload.get("tool_use_id") or payload.get("event_id") or "")
    return extract_event(
        text, event_name=event_name, session=session,
        actor=_runtime_actor(payload),
        tool_name=tool_name, source_ref=source_ref, response=response_text,
        ts=payload["ts"] if "ts" in payload else payload.get("timestamp"),
        extra={"targets": targets, "model_version": payload.get("model") or "unknown",
               "event_id": payload.get("tool_use_id") or payload.get("event_id") or "",
               "action_id": payload.get("tool_use_id") or payload.get("call_id") or "",
               "outcome_hint": outcome_hint,
               "features": tuple(sorted(extra_features)),
               "automation": bool(payload.get("automation")),
               "self_generated": bool(payload.get("_error_graph")),
               "projection_truncated": projection_truncated,
               "episode": payload.get("turn_id") or session},
    )


def session_db_path(event: Event) -> str:
    return os.path.join(RUNTIME_DIR, "sessions", event.session_hash + ".sqlite3")


def process_payload(payload: Mapping[str, Any], *, dry_run: bool = False,
                    explicit_mode: Optional[str] = None, db_path: Optional[str] = None,
                    prepared_event: Any = _UNSET) -> Tuple[Optional[Event], Optional[EngineResult], Optional[str]]:
    event = event_from_payload(payload) if prepared_event is _UNSET else prepared_event
    if event is None:
        return None, None, None
    mode = mode_from_gates(explicit_mode)
    store = MemoryStore() if dry_run else GraphStore(db_path or session_db_path(event), fail_open=True)
    engine = ErrorGraphEngine(store, mode=mode, fail_open=True)
    result = engine.process(event)
    message = engine.remediation_message(result.visible[0]) if result.visible else None
    return event, result, message


def hook_output(payload: Mapping[str, Any], result: EngineResult, message: Optional[str]) -> Optional[Dict[str, Any]]:
    if not message or not result.visible:
        return None
    event_name = str(payload.get("hook_event_name") or payload.get("event_name") or "")
    if event_name == "Stop":
        if result.should_block:
            return {"decision": "block", "reason": message}
        return None
    if event_name == "PreToolUse" and result.should_block:
        return {"hookSpecificOutput": {"hookEventName": event_name,
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": message}}
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": message}}


def _read_hook_payload(stream: Any = None, max_bytes: int = MAX_HOOK_PAYLOAD_BYTES) -> Optional[Mapping[str, Any]]:
    """Read one strictly bounded JSON payload; oversized hooks fail open."""
    return _read_hook_payload_result(stream, max_bytes)[0]


def _strict_json_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-standard JSON constant: %s" % value)


def _read_hook_payload_result(stream: Any = None, max_bytes: int = MAX_HOOK_PAYLOAD_BYTES) -> Tuple[Optional[Mapping[str, Any]], str]:
    """Return a payload plus an allow-listed health status."""
    source = stream if stream is not None else sys.stdin
    source = getattr(source, "buffer", source)
    raw = source.read(max(1, int(max_bytes)) + 1)
    if isinstance(raw, bytes):
        if len(raw) > max_bytes:
            return None, "oversize"
        text = raw.decode("utf-8", "strict")
    else:
        text = str(raw or "")
        if len(text.encode("utf-8", "replace")) > max_bytes:
            return None, "oversize"
    value = json.loads(
        text or "{}",
        parse_constant=_reject_json_constant,
        object_pairs_hook=_strict_json_object,
    )
    return (value, "ok") if isinstance(value, Mapping) else (None, "error")


def _metric_surface(payload: Mapping[str, Any]) -> str:
    event_name = str(payload.get("hook_event_name") or payload.get("event_name") or "")
    return _METRIC_SURFACES.get(event_name, "unknown")


def _metric_runtime(payload: Mapping[str, Any], runtime_hint: Optional[str]) -> str:
    candidates = (
        runtime_hint,
        os.environ.get("ERROR_GRAPH_RUNTIME_HINT"),
        os.environ.get("ERROR_GRAPH_RUNTIME"),
        payload.get("runtime"),
    )
    for candidate in candidates:
        normalized = str(candidate or "").strip().lower()
        if normalized in _METRIC_RUNTIMES:
            return normalized
    if "last_assistant_message" in payload or (payload.get("turn_id") and payload.get("model")):
        return "codex"
    return "unknown"


def _record_hook_metric(surface: str, runtime: str, mode: str,
                        status: str, latency_ms: float,
                        projection_truncated: bool = False) -> bool:
    """Keep telemetry failure strictly outside the hook's control flow."""
    try:
        return MetricsStore(METRICS_PATH).record(
            surface, runtime, mode, status, latency_ms,
            projection_truncated=projection_truncated)
    except Exception:
        return False


def hook_main(runtime_hint: Optional[str] = None) -> int:
    started = time.perf_counter()
    payload: Mapping[str, Any] = {}
    surface = "unknown"
    runtime = _metric_runtime(payload, runtime_hint)
    mode = mode_from_gates()
    status = "error"
    projection_truncated = False
    try:
        payload, read_status = _read_hook_payload_result()
        if payload is None:
            status = read_status
            return 0
        surface = _metric_surface(payload)
        runtime = _metric_runtime(payload, runtime_hint)
        mode = mode_from_gates()
        if runtime_hint and not payload.get("runtime") and not payload.get("agent_id"):
            payload = dict(payload)
            payload["runtime"] = runtime_hint
        prepared_event = event_from_payload(payload)
        projection_truncated = bool(
            prepared_event and prepared_event.attrs.get("projection_truncated") is True)
        event, result, message = process_payload(payload, prepared_event=prepared_event)
        if event is None or result is None:
            status = "ok"
            return 0
        if result.error_type or not result.persistence_ok:
            error_name = str(result.error_type or "persistence_error").lower()
            status = "timeout" if "timeout" in error_name else "error"
        else:
            status = "ok"
        output = hook_output(payload, result, message)
        if output:
            sys.stdout.write(json.dumps(output, ensure_ascii=True))
            # The optional health write runs afterward; never leave a deny or
            # advisory buffered behind telemetry if the host enforces timeout.
            sys.stdout.flush()
    except TimeoutError:
        status = "timeout"
    except Exception:
        status = "error"
    finally:
        latency_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        try:
            _record_hook_metric(surface, runtime, mode, status, latency_ms,
                                projection_truncated)
        except Exception:
            pass
    return 0


def cli_main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect one prospective-error event")
    parser.add_argument("--event", default="PreToolUse")
    parser.add_argument("--text", default="")
    parser.add_argument("--response", default="")
    parser.add_argument("--tool", default="")
    parser.add_argument("--session", default="dry-run")
    parser.add_argument("--mode", choices=("off", "shadow", "advisory", "block"), default="advisory")
    parser.add_argument("--db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    payload = {"event_name": args.event, "text": args.text, "response": args.response,
               "tool": args.tool, "session": args.session, "runtime": "cli"}
    event, result, message = process_payload(payload, dry_run=args.dry_run, explicit_mode=args.mode, db_path=args.db)
    public = {"event": event.public_record() if event else None,
              "warnings": [warning.to_record() for warning in (result.warnings if result else ())],
              "visible": [warning.trace_id for warning in (result.visible if result else ())],
              "should_block": bool(result and result.should_block), "message": message,
              "error_type": result.error_type if result else None}
    print(json.dumps(public, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--hook-runtime" and sys.argv[2] in {"claude", "codex"}:
        raise SystemExit(hook_main(sys.argv[2]))
    if len(sys.argv) > 1:
        raise SystemExit(cli_main())
    raise SystemExit(hook_main())
