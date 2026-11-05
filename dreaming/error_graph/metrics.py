#!/usr/bin/env python3
"""Small, privacy-safe rolling health counters for the live hook path.

The database deliberately contains only allow-listed categorical labels,
five-minute buckets, counters, and cumulative latency bins. It never receives
prompt text, tool arguments, session identifiers, hashes, or warning traces.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


BUCKET_SECONDS = 5 * 60
RETENTION_SECONDS = 30 * 24 * 60 * 60
SCHEMA_VERSION = 2
MAX_LATENCY_MS = 24 * 60 * 60 * 1000
MAX_COUNTER = (1 << 63) - 1
MAX_EXACT_FLOAT_INT = 1 << 53
LATENCY_THRESHOLDS_MS: Tuple[int, ...] = (
    1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000,
)
SURFACES = frozenset({"prompt", "pre_action", "post_action", "report", "unknown"})
RUNTIMES = frozenset({"claude", "codex", "cli", "unknown"})
MODES = frozenset({"off", "shadow", "advisory", "block"})
STATUSES = frozenset({"ok", "error", "oversize", "timeout"})
TABLE = "hook_health_5m"

_BIN_COLUMNS = tuple("latency_le_%d" % threshold for threshold in LATENCY_THRESHOLDS_MS)
_ALL_BIN_COLUMNS = _BIN_COLUMNS + ("latency_inf",)
_COUNTER_COLUMNS = (
    "calls", "errors", "oversized", "timeouts", "projection_truncated",
    "latency_count", "latency_sum_ms", "latency_max_ms",
) + _ALL_BIN_COLUMNS
_INTEGER_COUNTER_COLUMNS = tuple(
    name for name in _COUNTER_COLUMNS
    if name not in {"latency_sum_ms", "latency_max_ms"}
)


def _sql_labels(values: frozenset[str]) -> str:
    return ",".join("'%s'" % value for value in sorted(values))


def _schema_sql() -> str:
    bins = ",\n    ".join(
        "%s INTEGER NOT NULL DEFAULT 0 CHECK(%s >= 0)" % (name, name)
        for name in _ALL_BIN_COLUMNS)
    return """CREATE TABLE IF NOT EXISTS %s (
    bucket_start_s INTEGER NOT NULL CHECK(bucket_start_s >= 0 AND bucket_start_s %% %d = 0),
    surface TEXT NOT NULL CHECK(surface IN (%s)),
    runtime TEXT NOT NULL CHECK(runtime IN (%s)),
    mode TEXT NOT NULL CHECK(mode IN (%s)),
    calls INTEGER NOT NULL DEFAULT 0 CHECK(calls >= 0),
    errors INTEGER NOT NULL DEFAULT 0 CHECK(errors >= 0),
    oversized INTEGER NOT NULL DEFAULT 0 CHECK(oversized >= 0),
    timeouts INTEGER NOT NULL DEFAULT 0 CHECK(timeouts >= 0),
    projection_truncated INTEGER NOT NULL DEFAULT 0 CHECK(projection_truncated >= 0),
    latency_count INTEGER NOT NULL DEFAULT 0 CHECK(latency_count >= 0),
    latency_sum_ms REAL NOT NULL DEFAULT 0 CHECK(latency_sum_ms >= 0),
    latency_max_ms REAL NOT NULL DEFAULT 0 CHECK(latency_max_ms >= 0),
    %s,
    PRIMARY KEY (bucket_start_s, surface, runtime, mode)
) WITHOUT ROWID""" % (
        TABLE, BUCKET_SECONDS, _sql_labels(SURFACES), _sql_labels(RUNTIMES),
        _sql_labels(MODES), bins)


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _valid_label(value: Any, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def _nonnegative_integer(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= MAX_COUNTER else None
    if not isinstance(value, float):
        return None
    number = _finite_number(value)
    if (number is None or number < 0 or number > MAX_EXACT_FLOAT_INT
            or not number.is_integer()):
        return None
    return int(number)


def _validated_row(row: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate one legacy/current bucket before aggregation or publication."""
    if not (_valid_label(row.get("surface"), SURFACES)
            and _valid_label(row.get("runtime"), RUNTIMES)
            and _valid_label(row.get("mode"), MODES)):
        return None
    bucket = _nonnegative_integer(row.get("bucket_start_s"))
    if bucket is None or bucket % BUCKET_SECONDS:
        return None
    result: Dict[str, Any] = {
        "bucket_start_s": bucket,
        "surface": row["surface"], "runtime": row["runtime"], "mode": row["mode"]}
    for name in _INTEGER_COUNTER_COLUMNS:
        value = _nonnegative_integer(row.get(name))
        if value is None:
            return None
        result[name] = value
    for name in ("latency_sum_ms", "latency_max_ms"):
        value = _finite_number(row.get(name))
        if value is None or value < 0:
            return None
        result[name] = value

    calls = result["calls"]
    latency_count = result["latency_count"]
    if latency_count != calls or result["projection_truncated"] > calls:
        return None
    if result["errors"] + result["oversized"] + result["timeouts"] > calls:
        return None
    cumulative = [result[name] for name in _BIN_COLUMNS] + [result["latency_inf"]]
    if any(left > right for left, right in zip(cumulative, cumulative[1:])):
        return None
    if result["latency_inf"] != latency_count:
        return None
    if latency_count:
        maximum = result["latency_max_ms"]
        total = result["latency_sum_ms"]
        tolerance = max(1e-9, abs(total) * 1e-12)
        if (maximum > MAX_LATENCY_MS or maximum > total + tolerance
                or total > latency_count * maximum + tolerance
                or total > latency_count * MAX_LATENCY_MS + tolerance):
            return None
        for threshold, name in zip(LATENCY_THRESHOLDS_MS, _BIN_COLUMNS):
            if (maximum <= threshold and result[name] != latency_count) \
                    or (maximum > threshold and result[name] >= latency_count):
                return None
        lower_sum = 0.0
        upper_sum = 0.0
        previous_count = 0
        previous_threshold = 0.0
        for threshold, name in zip(LATENCY_THRESHOLDS_MS, _BIN_COLUMNS):
            interval_count = result[name] - previous_count
            lower_sum += interval_count * previous_threshold
            upper_sum += interval_count * threshold
            previous_count = result[name]
            previous_threshold = float(threshold)
        tail_count = latency_count - previous_count
        lower_sum += tail_count * previous_threshold
        upper_sum += tail_count * MAX_LATENCY_MS
        if total < lower_sum - tolerance or total > upper_sum + tolerance:
            return None
    elif result["latency_sum_ms"] != 0 or result["latency_max_ms"] != 0:
        return None
    return result


class MetricsStore:
    """Atomic five-minute aggregates with a short, fail-open SQLite budget."""

    def __init__(self, path: str, *, timeout_ms: int = 50,
                 retention_s: int = RETENTION_SECONDS,
                 bucket_s: int = BUCKET_SECONDS):
        self.path = os.path.abspath(os.path.expanduser(str(path)))
        self.timeout_ms = max(1, int(timeout_ms))
        if isinstance(bucket_s, bool) or int(bucket_s) != BUCKET_SECONDS:
            raise ValueError("health bucket size is fixed at %d seconds" % BUCKET_SECONDS)
        self.retention_s = max(BUCKET_SECONDS, int(retention_s))
        self.bucket_s = BUCKET_SECONDS

    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        con: Optional[sqlite3.Connection] = None
        try:
            con = sqlite3.connect(self.path, timeout=self.timeout_ms / 1000.0,
                                  isolation_level=None)
            con.execute("PRAGMA busy_timeout=%d" % self.timeout_ms)
            try:
                con.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                # journal_mode is a connection preference, not part of the
                # health-record transaction.  During the first concurrent
                # legacy migration SQLite can reject this PRAGMA immediately
                # even though busy_timeout is set; BEGIN IMMEDIATE below can
                # still serialize the writers correctly in the existing mode.
                # Do not turn one harmless migration race into lost telemetry.
                detail = str(exc).lower()
                if "locked" not in detail and "busy" not in detail:
                    raise
            con.execute("PRAGMA synchronous=NORMAL")
            return con
        except Exception:
            # If initialization itself fails, assignment in ``record`` never
            # occurs. Close here so fail-open also means no leaked Windows
            # handle on a corrupt, read-only, or incompatible database.
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass
            raise

    def record(self, surface: str, runtime: str, mode: str, status: str,
               latency_ms: float, *, now: Optional[float] = None,
               projection_truncated: bool = False) -> bool:
        """Record one invocation; invalid labels fail before creating a file."""
        if not (_valid_label(surface, SURFACES)
                and _valid_label(runtime, RUNTIMES)
                and _valid_label(mode, MODES)
                and _valid_label(status, STATUSES)
                and isinstance(projection_truncated, bool)):
            return False
        latency = _finite_number(latency_ms)
        timestamp = _finite_number(time.time() if now is None else now)
        if (latency is None or latency < 0 or latency > MAX_LATENCY_MS
                or timestamp is None or timestamp < 0):
            return False

        bucket = int(timestamp // self.bucket_s) * self.bucket_s
        counters = {
            "calls": 1,
            "errors": int(status == "error"),
            "oversized": int(status == "oversize"),
            "timeouts": int(status == "timeout"),
            "projection_truncated": int(projection_truncated),
            "latency_count": 1,
            "latency_sum_ms": latency,
            "latency_max_ms": latency,
            "latency_inf": 1,
        }
        for threshold, column in zip(LATENCY_THRESHOLDS_MS, _BIN_COLUMNS):
            counters[column] = int(latency <= threshold)

        columns = ("bucket_start_s", "surface", "runtime", "mode") + _COUNTER_COLUMNS
        values = (bucket, surface, runtime, mode) + tuple(counters[name] for name in _COUNTER_COLUMNS)
        updates = []
        for name in _COUNTER_COLUMNS:
            if name == "latency_max_ms":
                updates.append("%s=MAX(%s, excluded.%s)" % (name, name, name))
            else:
                updates.append("%s=%s + excluded.%s" % (name, name, name))
        sql = ("INSERT INTO %s (%s) VALUES (%s) "
               "ON CONFLICT(bucket_start_s, surface, runtime, mode) DO UPDATE SET %s"
               % (TABLE, ",".join(columns), ",".join("?" for _ in columns),
                  ",".join(updates)))

        con: Optional[sqlite3.Connection] = None
        try:
            con = self._connect()
            con.execute("BEGIN IMMEDIATE")
            current_version = int(con.execute("PRAGMA user_version").fetchone()[0])
            if current_version > SCHEMA_VERSION:
                raise RuntimeError("health database schema is newer than this runtime")
            con.execute(_schema_sql())
            columns = {row[1] for row in con.execute("PRAGMA table_info(%s)" % TABLE)}
            if "projection_truncated" not in columns:
                con.execute(
                    "ALTER TABLE %s ADD COLUMN projection_truncated "
                    "INTEGER NOT NULL DEFAULT 0 CHECK(projection_truncated >= 0)" % TABLE)
            con.execute("PRAGMA user_version=%d" % SCHEMA_VERSION)
            con.execute(sql, values)
            con.execute(
                "DELETE FROM %s WHERE bucket_start_s < ? OR bucket_start_s > ?" % TABLE,
                (bucket - self.retention_s, bucket))
            con.execute("COMMIT")
            return True
        except Exception:
            if con is not None:
                try:
                    con.execute("ROLLBACK")
                except Exception:
                    pass
            return False
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass


def _empty_summary(since_s: Optional[float]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "bucket_seconds": BUCKET_SECONDS,
        "lookback_seconds": since_s,
        "first_bucket_s": None,
        "last_bucket_s": None,
        "invalid_rows": 0,
        "groups": [],
        "total": {
            "calls": 0, "errors": 0, "oversized": 0, "timeouts": 0,
            "projection_truncated": 0,
            "latency_count": 0, "latency_mean_ms": None,
            "latency_max_ms": None, "p50_upper_ms": None,
            "p95_upper_ms": None, "latency_over_5000": 0,
        },
    }


def _quantile_upper(row: Mapping[str, Any], quantile: float) -> Optional[float]:
    count = int(row.get("latency_count") or 0)
    if count <= 0:
        return None
    rank = max(1, int(math.ceil(count * quantile)))
    for threshold, column in zip(LATENCY_THRESHOLDS_MS, _BIN_COLUMNS):
        if int(row.get(column) or 0) >= rank:
            return float(threshold)
    return None


def _public_group(row: Mapping[str, Any], *, include_labels: bool) -> Dict[str, Any]:
    count = int(row.get("latency_count") or 0)
    result: Dict[str, Any] = {}
    if include_labels:
        result.update({name: row[name] for name in ("surface", "runtime", "mode")})
    for name in ("calls", "errors", "oversized", "timeouts",
                 "projection_truncated", "latency_count"):
        result[name] = int(row.get(name) or 0)
    total_ms = float(row.get("latency_sum_ms") or 0.0)
    result["latency_mean_ms"] = round(total_ms / count, 3) if count else None
    result["latency_max_ms"] = round(float(row.get("latency_max_ms") or 0.0), 3) if count else None
    result["p50_upper_ms"] = _quantile_upper(row, 0.50)
    result["p95_upper_ms"] = _quantile_upper(row, 0.95)
    result["latency_over_5000"] = max(
        0, int(row.get("latency_inf") or 0) - int(row.get("latency_le_5000") or 0))
    return result


def summarize(path: str, *, since_s: Optional[float] = None,
              now: Optional[float] = None) -> Dict[str, Any]:
    """Read aggregate health without creating or mutating a missing database."""
    if since_s is None:
        lookback = float(RETENTION_SECONDS)
    else:
        lookback = _finite_number(since_s)
    timestamp = _finite_number(time.time() if now is None else now)
    if lookback is None or lookback < 0 or timestamp is None or timestamp < 0:
        return _empty_summary(None)
    lookback = min(lookback, float(RETENTION_SECONDS))
    report = _empty_summary(lookback)
    resolved = Path(os.path.abspath(os.path.expanduser(str(path))))
    if not resolved.is_file():
        return report

    current_bucket = int(timestamp // BUCKET_SECONDS) * BUCKET_SECONDS
    cutoff = max(0.0, timestamp - lookback)
    lower_bucket = int(cutoff // BUCKET_SECONDS) * BUCKET_SECONDS
    time_where = "bucket_start_s >= ? AND bucket_start_s <= ?"
    label_values = tuple(sorted(SURFACES)) + tuple(sorted(RUNTIMES)) + tuple(sorted(MODES))
    label_where = (
        "surface IN (%s) AND runtime IN (%s) AND mode IN (%s)" % (
            ",".join("?" for _ in SURFACES),
            ",".join("?" for _ in RUNTIMES),
            ",".join("?" for _ in MODES),
        ))
    params: Tuple[Any, ...] = (lower_bucket, current_bucket) + label_values
    con: Optional[sqlite3.Connection] = None
    try:
        con = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True,
                              timeout=0.05, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (TABLE,)).fetchone():
            return report
        columns = {row[1] for row in con.execute("PRAGMA table_info(%s)" % TABLE)}
        required = {"bucket_start_s", "surface", "runtime", "mode"} | \
            (set(_COUNTER_COLUMNS) - {"projection_truncated"})
        if not required.issubset(columns):
            return report
        selected = ",".join(
            ("0 AS projection_truncated" if name == "projection_truncated"
             and name not in columns else
             name)
            for name in _COUNTER_COLUMNS
        )
        total_rows = int(con.execute(
            "SELECT COUNT(*) FROM %s WHERE %s" % (TABLE, time_where),
            (lower_bucket, current_bucket)).fetchone()[0])
        query = ("SELECT bucket_start_s,surface,runtime,mode,%s FROM %s "
                 "WHERE %s AND %s ORDER BY bucket_start_s,runtime,surface,mode"
                 % (selected, TABLE, time_where, label_where))
        rows = [dict(row) for row in con.execute(query, params).fetchall()]
    except Exception:
        return report
    finally:
        if con is not None:
            try:
                if con.in_transaction:
                    con.execute("ROLLBACK")
            except Exception:
                pass
            try:
                con.close()
            except Exception:
                pass

    validated = [item for row in rows if (item := _validated_row(row)) is not None]
    report["invalid_rows"] = max(0, total_rows - len(validated))
    if validated:
        report["first_bucket_s"] = min(row["bucket_start_s"] for row in validated)
        report["last_bucket_s"] = max(row["bucket_start_s"] for row in validated)

    grouped: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for row in validated:
        key = (row["surface"], row["runtime"], row["mode"])
        group = grouped.setdefault(
            key, {"surface": key[0], "runtime": key[1], "mode": key[2],
                  **{name: 0 for name in _COUNTER_COLUMNS}})
        for name in _COUNTER_COLUMNS:
            if name == "latency_max_ms":
                group[name] = max(float(group[name]), float(row[name]))
            else:
                group[name] += row[name]
    ordered = [grouped[key] for key in sorted(grouped, key=lambda key: (key[1], key[0], key[2]))]
    report["groups"] = [_public_group(row, include_labels=True) for row in ordered]
    combined: Dict[str, Any] = {name: 0 for name in _COUNTER_COLUMNS}
    for row in ordered:
        for name in _COUNTER_COLUMNS:
            if name == "latency_max_ms":
                combined[name] = max(float(combined[name]), float(row.get(name) or 0.0))
            else:
                combined[name] += row.get(name) or 0
    report["total"] = _public_group(combined, include_labels=False)
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize privacy-safe hook health counters")
    parser.add_argument("--path", required=True)
    parser.add_argument("--since-hours", type=float, default=24.0)
    args = parser.parse_args(argv)
    print(json.dumps(summarize(args.path, since_s=max(0.0, args.since_hours) * 3600.0),
                     ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
