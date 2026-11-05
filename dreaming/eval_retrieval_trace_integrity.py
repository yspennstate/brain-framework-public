#!/usr/bin/env python3
"""Synthetic, read-only contract gate for build_retrieval_trace.py.

The evaluator writes only inside a TemporaryDirectory. It never invokes the builder's live defaults
and never touches retrievals.tsv. It verifies recursive subagent inclusion, parent-session attribution,
identity-safe exact deduplication, wall-clock windows, future exclusion, argument rejection, deterministic
ordering/serialization, and fail-closed behavior when the required transcript root is absent.

Usage: python eval_retrieval_trace_integrity.py
"""
from __future__ import print_function

import contextlib
import io
import json
import os
import tempfile

import build_retrieval_trace as trace
import sys


AS_OF_ISO = "2026-07-09T12:00:00Z"
AS_OF = trace._epoch(AS_OF_ISO)
MEMORY_ROOT = "~/.claude/projects/{{BRAIN_SLUG}}/memory"


def _iso(offset_seconds):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(AS_OF + offset_seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def _record(slug, timestamp, uuid, tool_id, session="session-parent"):
    item = {
        "type": "tool_use",
        "name": "Read",
        "input": {"file_path": "%s/%s.md" % (MEMORY_ROOT, slug)},
    }
    if tool_id is not None:
        item["id"] = tool_id
    row = {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {"role": "assistant", "content": [item]},
    }
    if uuid is not None:
        row["uuid"] = uuid
    if session is not None:
        row["sessionId"] = session
    return row


def _write_jsonl(path, records):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def _arg_rejected(argv):
    sink = io.StringIO()
    try:
        with contextlib.redirect_stderr(sink):
            trace._args(argv)
    except SystemExit as exc:
        return exc.code == 2
    return False


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    passed = []
    failed = []

    def check(name, condition, detail=""):
        (passed if condition else failed).append((name, detail))

    with tempfile.TemporaryDirectory(prefix="eval-retrieval-trace-") as root:
        main_path = os.path.join(root, "session-parent.jsonl")
        nested_path = os.path.join(root, "session-parent", "subagents", "agent-one.jsonl")

        duplicate = _record("duplicate_event", _iso(-86400), "uuid-duplicate", "tool-duplicate")
        root_records = [
            _record("boundary", _iso(-2 * 86400), "uuid-boundary", "tool-boundary"),
            duplicate,
            duplicate,  # exact repeated transcript record in one file
            _record("repeat", _iso(-18 * 3600), "uuid-repeat-a", "tool-repeat-a"),
            _record("repeat", _iso(-18 * 3600), "uuid-repeat-b", "tool-repeat-b"),
            _record("old_history", _iso(-3 * 86400), "uuid-old", "tool-old"),
            _record("future", _iso(1), "uuid-future", "tool-future"),
            _record("bad_timestamp", "not-a-time", "uuid-bad", "tool-bad"),
            _record("MEMORY", _iso(-3600), "uuid-special", "tool-special"),
            _record("dream_synthetic", _iso(-3600), "uuid-dream", "tool-dream"),
        ]
        nested_records = [
            duplicate,  # copied event in another transcript must still count once
            _record("nested_unique", _iso(-12 * 3600), "uuid-nested", "tool-nested"),
            _record("fallback_nested", _iso(-6 * 3600), "uuid-fallback", "tool-fallback", session=None),
        ]
        _write_jsonl(main_path, root_records)
        _write_jsonl(nested_path, nested_records)

        rows, stats = trace.build_rows(root=root, days=2.0, as_of=AS_OF)
        rows_again, stats_again = trace.build_rows(root=root, days=2.0, as_of=AS_OF)
        slugs = [row[1] for row in rows]
        sessions = {row[3] for row in rows}

        check("nested subagent included", slugs.count("nested_unique") == 1)
        check("nested parent session", sessions == {"session-parent"}, repr(sessions))
        check("nested path fallback session", slugs.count("fallback_nested") == 1)
        check("nested row metric", stats["nested_rows"] == 2, str(stats["nested_rows"]))
        check("root row metric", stats["root_rows"] == 4, str(stats["root_rows"]))
        check("exact duplicate collapsed", slugs.count("duplicate_event") == 1)
        check("duplicate metric", stats["duplicate_reads"] == 2, str(stats["duplicate_reads"]))
        check("distinct tied reads retained", slugs.count("repeat") == 2)
        check("wall-clock boundary inclusive", slugs.count("boundary") == 1)
        check("old event excluded", "old_history" not in slugs)
        check("future event excluded", "future" not in slugs)
        check("bad timestamp excluded", "bad_timestamp" not in slugs)
        check("special and dream pages excluded", "MEMORY" not in slugs and "dream_synthetic" not in slugs)
        check("window row count", len(rows) == 6, str(len(rows)))
        check("stable rows", rows == rows_again)
        check("stable stats", stats == stats_again)
        check("chronological ordering", [row[0] for row in rows] == sorted(row[0] for row in rows))

        all_rows, _all_stats = trace.build_rows(root=root, days=None, as_of=AS_OF)
        all_slugs = [row[1] for row in all_rows]
        check("default retains old history", "old_history" in all_slugs)
        check("default still excludes future", "future" not in all_slugs)

        out = os.path.join(root, "synthetic-retrievals.tsv")
        trace._write_trace(rows, out)
        first_bytes = io.open(out, "rb").read()
        trace._write_trace(rows_again, out)
        second_bytes = io.open(out, "rb").read()
        leftovers = [name for name in os.listdir(root) if name.startswith("synthetic-retrievals.tsv.tmp.")]
        check("stable serialization", first_bytes == second_bytes)
        check("header and row count", first_bytes.count(b"\n") == len(rows) + 1)
        check("atomic temp cleanup", not leftovers, repr(leftovers))

        for bad in ("0", "-1", "nan", "inf", "-inf", "not-a-number"):
            check("reject --days %s" % bad, _arg_rejected(["--days", bad]))
        check("reject missing --days value", _arg_rejected(["--days"]))
        check("accept fractional positive days", trace._args(["--days", "0.5"]).days == 0.5)
        check("default is full history", trace._args([]).days is None)

        sentinel = os.path.join(root, "sentinel.tsv")
        with io.open(sentinel, "w", encoding="utf-8") as handle:
            handle.write("do-not-replace\n")
        before = io.open(sentinel, "rb").read()
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            rc = trace.main([], transcript_dir=os.path.join(root, "missing-root"),
                            output=sentinel, as_of=AS_OF)
        after = io.open(sentinel, "rb").read()
        check("missing root exit code", rc == 2, str(rc))
        check("missing root leaves output untouched", before == after)
        try:
            trace.build_rows(root=os.path.join(root, "missing-root"), as_of=AS_OF)
        except FileNotFoundError:
            direct_missing_failed = True
        else:
            direct_missing_failed = False
        check("direct builder missing root fails closed", direct_missing_failed)

    print("eval_retrieval_trace_integrity: %d/%d checks PASS" % (len(passed), len(passed) + len(failed)))
    print("  synthetic window: %d rows; nested=%d; exact duplicates removed=%d; future=%d; old=%d" % (
        len(rows), stats["nested_rows"], stats["duplicate_reads"], stats["future_reads"],
        stats["too_old_reads"],
    ))
    if failed:
        for name, detail in failed:
            print("  FAIL: %s%s" % (name, " - " + detail if detail else ""))
        return 1
    print("VERDICT: PASS - trace construction is recursive, identity-safe, wall-clock bounded, deterministic, and fail-closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
