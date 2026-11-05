#!/usr/bin/env python3
"""Strict, privacy-safe reader and renderer for latest mistake-watch telemetry.

The producer owns publication. This module is deliberately read-only and
stdlib-only so both wake surfaces can consume one exact wire contract without
reading the private snapshot or exposing its retry-binding digest.
"""

import json
import math
import os
import re
import time
from dataclasses import dataclass


STATUS_SCHEMA = "mistake-graph-status/v1"
GENERATION_SCHEMA = "mistake-graph-generation/v1"
STATUS_MAX_BYTES = 1024
GENERATION_MAX_BYTES = 256
STATUS_SIGNAL_MAX = 1_000_000
STATUS_READ_ATTEMPTS = 3
STALE_AFTER_SECONDS = 30 * 3600
FUTURE_SKEW_SECONDS = 5 * 60
STATUS_STATES = frozenset(("baseline", "stale", "no_warning", "actionable"))
STATUS_SIGNAL_FIELDS = (
    "became_recurring", "new_corrections", "new_overrides", "causal_removals",
)


class StatusError(RuntimeError):
    """The latest-watch status was absent, unstable, or invalid."""


class _ConcurrentReplacement(RuntimeError):
    pass


@dataclass(frozen=True)
class WatchSummary:
    title: str
    detail: str
    line: str
    level: str


def _json_loads_no_duplicates(text):
    def object_from_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    return json.loads(text, object_pairs_hook=object_from_pairs)


def canonical_status(payload):
    if (not isinstance(payload, dict)
            or set(payload) != {
                "schema", "generation", "snapshot_sha256", "state", "signals",
                "carried",
            }
            or payload.get("schema") != STATUS_SCHEMA):
        raise StatusError("invalid status schema")
    generation = payload.get("generation")
    digest = payload.get("snapshot_sha256")
    state = payload.get("state")
    signals = payload.get("signals")
    carried = payload.get("carried")
    if type(generation) is not int or not 0 <= generation < (1 << 63):
        raise StatusError("invalid status generation")
    if (not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None):
        raise StatusError("invalid status digest")
    if not isinstance(state, str) or state not in STATUS_STATES:
        raise StatusError("invalid status state")
    if type(carried) is not bool:
        raise StatusError("invalid status carry marker")
    if not isinstance(signals, dict) or set(signals) != set(STATUS_SIGNAL_FIELDS):
        raise StatusError("invalid status signals")
    clean_signals = {}
    for field in STATUS_SIGNAL_FIELDS:
        value = signals.get(field)
        if type(value) is not int or not 0 <= value <= STATUS_SIGNAL_MAX:
            raise StatusError("invalid status signal")
        if field == "causal_removals" and value not in (0, 1):
            raise StatusError("invalid causal-removal marker")
        clean_signals[field] = value
    actionable = any(clean_signals.values())
    if (state == "actionable") != actionable:
        raise StatusError("status actionability mismatch")
    if carried and state != "actionable":
        raise StatusError("carried status is not actionable")
    return {
        "schema": STATUS_SCHEMA,
        "generation": generation,
        "snapshot_sha256": digest,
        "state": state,
        "signals": clean_signals,
        "carried": carried,
    }


def decode_status(raw):
    try:
        payload = _json_loads_no_duplicates(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StatusError("malformed status") from exc
    return canonical_status(payload)


def _signature(stat_result):
    return (
        getattr(stat_result, "st_dev", None),
        getattr(stat_result, "st_ino", None),
        stat_result.st_size,
        getattr(stat_result, "st_mtime_ns", None),
    )


def _read_consistent_bytes_once(path):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if before.st_size > STATUS_MAX_BYTES:
            try:
                path_after = os.stat(path)
            except FileNotFoundError as exc:
                raise _ConcurrentReplacement() from exc
            if _signature(before) != _signature(path_after):
                raise _ConcurrentReplacement()
            raise StatusError("status exceeds byte bound")

        chunks = []
        total = 0
        while total <= STATUS_MAX_BYTES:
            chunk = os.read(fd, STATUS_MAX_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        try:
            path_after = os.stat(path)
        except FileNotFoundError as exc:
            raise _ConcurrentReplacement() from exc
        if (len(raw) > STATUS_MAX_BYTES
                or _signature(before) != _signature(after)
                or _signature(after) != _signature(path_after)
                or len(raw) != after.st_size):
            raise _ConcurrentReplacement()
        return raw, after.st_mtime
    finally:
        os.close(fd)


def _read_consistent_bytes(path, attempts=STATUS_READ_ATTEMPTS):
    last_error = None
    for _attempt in range(max(1, int(attempts))):
        try:
            return _read_consistent_bytes_once(path)
        except (FileNotFoundError, PermissionError, _ConcurrentReplacement) as exc:
            last_error = exc
        except StatusError:
            raise
        except OSError as exc:
            raise StatusError("status read failed") from exc
    raise StatusError("status unavailable or changing") from last_error


def load_status(path):
    raw, mtime = _read_consistent_bytes(os.fspath(path))
    return decode_status(raw), mtime


def decode_attempt_generation(raw):
    try:
        payload = _json_loads_no_duplicates(raw.decode("utf-8", "strict"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StatusError("malformed attempt generation") from exc
    if (not isinstance(payload, dict)
            or set(payload) != {"schema", "value"}
            or payload.get("schema") != GENERATION_SCHEMA
            or type(payload.get("value")) is not int
            or not 0 <= payload["value"] < (1 << 63)):
        raise StatusError("invalid attempt-generation schema")
    return payload["value"]


def load_attempt_generation(path):
    raw, mtime = _read_consistent_bytes(os.fspath(path))
    if len(raw) > GENERATION_MAX_BYTES:
        raise StatusError("attempt generation exceeds byte bound")
    return decode_attempt_generation(raw), mtime


def _plural(count, singular, plural=None):
    return singular if count == 1 else (plural or singular + "s")


def _signal_text(status):
    signals = status["signals"]
    lower = "at least " if status["carried"] else ""
    parts = []
    count = signals["became_recurring"]
    if count:
        parts.append("%s%d %s became recurring" % (
            lower, count, _plural(count, "one-off")))
    count = signals["new_corrections"]
    if count:
        parts.append("%s%d new %s" % (
            lower, count, _plural(count, "correction")))
    count = signals["new_overrides"]
    if count:
        parts.append("%s%d new %s" % (
            lower, count, _plural(count, "override")))
    if signals["causal_removals"]:
        parts.append("causal-removal evidence needs review")
    return "; ".join(parts)


def _age_text(age_seconds):
    return "%.1fh old" % (max(0.0, age_seconds) / 3600.0)


def _summary(title, detail, status_label, level):
    return WatchSummary(
        title=title,
        detail=detail,
        line="MISTAKES  %s - %s" % (status_label, detail),
        level=level,
    )


def _unverified_summary():
    detail = ("no valid latest-watch status is available; do not infer that "
              "recurrence monitoring is quiet.")
    return _summary(
        "Mistake recurrence watch: FEED UNVERIFIED",
        detail, "FEED UNVERIFIED", "unverified")


def _attempt_gap_summary(status, attempt_generation, attempt_mtime, now):
    """Expose reserved attempts that never reached the published-status boundary."""
    published_generation = status["generation"]
    if attempt_generation < published_generation:
        return _unverified_summary()
    if attempt_generation == published_generation:
        return None
    if (not math.isfinite(attempt_mtime)
            or attempt_mtime > now + FUTURE_SKEW_SECONDS):
        return _unverified_summary()

    gap = attempt_generation - published_generation
    age_text = _age_text(max(0.0, now - attempt_mtime))
    attempt_word = _plural(gap, "attempt")
    if status["state"] == "actionable":
        detail = ("latest successful watch already reported: %s; %d later watch %s "
                  "did not publish a status (last attempt %s). Do not infer newer quiet."
                  % (_signal_text(status), gap, attempt_word, age_text))
        title = "Mistake recurrence watch: ACTION REQUIRED + ATTEMPT UNRESOLVED"
    else:
        detail = ("%d later watch %s did not publish a status (last attempt %s); "
                  "the observer may still be running or may have failed. Do not infer quiet."
                  % (gap, attempt_word, age_text))
        title = "Mistake recurrence watch: ATTEMPT UNRESOLVED"
    return _summary(
        title, detail, "ATTEMPT UNRESOLVED", "attempt_unresolved")


def summarize_status(status, mtime, now=None):
    status = canonical_status(status)
    now = time.time() if now is None else float(now)
    mtime = float(mtime)
    if not math.isfinite(now) or not math.isfinite(mtime):
        return _unverified_summary()
    if mtime > now + FUTURE_SKEW_SECONDS:
        return _unverified_summary()
    age = max(0.0, now - mtime)
    age_text = _age_text(age)
    state = status["state"]

    if age > STALE_AFTER_SECONDS:
        if state == "actionable":
            detail = "latest recorded watch (>30h old) reported: %s." % (
                _signal_text(status))
        elif state == "baseline":
            detail = ("latest recorded watch is >30h old and only observed an "
                      "initial baseline; do not infer current quiet.")
        elif state == "stale":
            detail = ("latest recorded watch is >30h old and was itself based "
                      "on an older observation; do not infer current quiet.")
        else:
            detail = ("latest recorded quiet watch is >30h old; do not infer "
                      "that current sources are quiet.")
        return _summary(
            "Mistake recurrence watch: FEED STALE",
            detail, "FEED STALE", "feed_stale")

    if state == "actionable":
        if status["carried"]:
            detail = ("latest watch carries an earlier unresolved warning: %s "
                      "(%s)." % (_signal_text(status), age_text))
        else:
            detail = "latest watch reported: %s (%s)." % (
                _signal_text(status), age_text)
        return _summary(
            "Mistake recurrence watch: ACTION REQUIRED",
            detail, "ACTION REQUIRED", "actionable")
    if state == "baseline":
        detail = ("latest watch observed an initial baseline; no recurrence "
                  "comparison was possible (%s)." % age_text)
        return _summary(
            "Mistake recurrence watch: BASELINE ONLY",
            detail, "BASELINE ONLY", "baseline")
    if state == "stale":
        detail = ("latest watch used an older observation and did not assess "
                  "current sources; do not infer quiet (%s)." % age_text)
        return _summary(
            "Mistake recurrence watch: OBSERVATION STALE",
            detail, "OBSERVATION STALE", "observation_stale")

    # v1 binds only the durable MISTAKES.md/instinct ledger diff. It has no
    # correction-candidate head, disposition head, or runtime-ingress coverage,
    # so a fresh re-read of unchanged source bytes cannot support "no new
    # correction." Preserve the useful diff fact but fail loud on ingestion.
    detail = (
        "latest durable-ledger diff found no new recurrence, correction, "
        "override, or causal-removal signals (%s), but this v1 status has no "
        "candidate/disposition watermark or Claude+Codex ingress coverage. "
        "Correction ingestion is unverified; do not infer that there were no "
        "new owner corrections." % age_text
    )
    return _summary(
        "Mistake recurrence watch: LEDGER DIFF ONLY - INGESTION UNVERIFIED",
        detail, "INGESTION UNVERIFIED", "ingestion_unverified")


def summarize_path(path, now=None, generation_path=None):
    try:
        status, mtime = load_status(path)
        now_value = time.time() if now is None else float(now)
        if generation_path is None:
            generation_path = os.path.join(
                os.path.dirname(os.path.abspath(os.fspath(path))),
                ".mistake_graph_snapshot.json.generation.json",
            )
        # Older producers did not publish the attempt counter. Preserve an
        # already-actionable warning, but never treat a missing counter as
        # evidence of quiet: absence is also what deletion/nonpublication
        # looks like. Once present, read/schema failures remain fail-loud.
        if not os.path.exists(generation_path):
            if status["state"] == "actionable":
                return summarize_status(status, mtime, now=now_value)
            return _unverified_summary()
        attempt_generation, attempt_mtime = load_attempt_generation(generation_path)
        gap_summary = _attempt_gap_summary(
            status, attempt_generation, attempt_mtime, now_value)
        if gap_summary is not None:
            return gap_summary
        return summarize_status(status, mtime, now=now_value)
    except Exception:
        return _unverified_summary()
