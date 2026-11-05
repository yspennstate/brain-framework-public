#!/usr/bin/env python3
"""Isolated regression for build_memory_access.py; uses only synthetic temporary transcripts."""

from __future__ import print_function

import contextlib
import csv
import io
import json
import os
import tempfile

import build_memory_access as access
import sys


CLAUDE_SESSION = "11111111-1111-4111-8111-111111111111"
CODEX_SESSION = "22222222-2222-4222-8222-222222222222"
TS = "2026-07-09T12:00:00Z"
EPOCH = 1783598400.0
MEM = os.path.expanduser(r"~/.claude/projects/{{BRAIN_SLUG}}/memory")


def _dump(path, rows):
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def _claude_fixture(path):
    read = {
        "timestamp": TS,
        "sessionId": CLAUDE_SESSION,
        "message": {"content": [
            {"type": "tool_use", "id": "read-alpha", "name": "Read",
             "input": {"file_path": MEM + r"\alpha_memory.md"}},
            {"type": "tool_use", "id": "shell-one", "name": "Bash", "input": {"command":
             "Get-Content -Raw -LiteralPath \"%s\\beta_memory.md\"; "
             "rg -n \"NEEDLE-RAW-MUST-NOT-LEAK\" \"%s\"; "
             "Get-Content \"%s\\theta_search_only.md\" | Select-String "
             "-Pattern \"PIPELINE-SEARCH-NOT-READ\"; "
             "python \"~\\.claude\\dreaming\\brain.py\" recall "
             "\"SUPERSECRET-QUERY-MUST-NOT-LEAK\"" % (MEM, MEM, MEM)}},
            # Neither merely naming a path nor Get-Item is evidence that its content was read.
            {"type": "tool_use", "id": "ambiguous", "name": "Bash", "input": {"command":
             "Get-Item \"%s\\theta_not_read.md\"; python inspect_memory.py "
             "\"%s\\iota_not_read.md\"" % (MEM, MEM)}},
            # Operator names inside prose are not executions; && chains are separate commands.
            {"type": "tool_use", "id": "mentions", "name": "Bash", "input": {"command":
             "Write-Output \"Get-Content %s\\mention_not_read.md\"; "
             "Write-Output \"grep %s\"; python -c \"print('brain.py recall')\"" % (MEM, MEM)}},
            {"type": "tool_use", "id": "chain", "name": "Bash", "input": {"command":
             "Get-Content \"%s\\chain_memory.md\" && rg needle \"%s\"" % (MEM, MEM)}},
        ]},
    }
    result = {
        "timestamp": "2026-07-09T12:00:01Z",
        "sessionId": CLAUDE_SESSION,
        "message": {"content": [{
            "type": "tool_result", "tool_use_id": "shell-one",
            "content": "recall: [redacted]\n  0.92 [lex+sem] gamma_surface\n"
                       "        description must not be retained\n"
                       "  0.71 [graph] second_surface\n",
        }]},
    }
    future = {
        "timestamp": "2099-01-01T00:00:00Z", "sessionId": CLAUDE_SESSION,
        "message": {"content": [{"type": "tool_use", "id": "future-read", "name": "Read",
                                    "input": {"file_path": MEM + r"\future_not_accessed.md"}}]},
    }
    old = {
        "timestamp": "2020-01-01T00:00:00Z", "sessionId": CLAUDE_SESSION,
        "message": {"content": [{"type": "tool_use", "id": "old-read", "name": "Read",
                                    "input": {"file_path": MEM + r"\old_not_accessed.md"}}]},
    }
    _dump(path, [read, read, result, future, old])  # duplicate exercises deterministic dedupe


def _codex_fixture(path):
    command = (
        "Get-Content -LiteralPath \"%s\\delta_memory.md\"; "
        "Select-String -Pattern \"RAW-SEARCH-MUST-NOT-LEAK\" -LiteralPath "
        "\"%s\\epsilon_search.md\"; "
        "python \"~\\.claude\\dreaming\\brain.py\" recall "
        "\"RAW-CODEX-QUERY-MUST-NOT-LEAK\"" % (MEM, MEM)
    )
    wrapper = "const r = await tools.shell_command({command:%s}); text(r);" % json.dumps(command)
    rows = [
        {"timestamp": TS, "type": "session_meta", "payload": {
            "id": CODEX_SESSION, "session_id": CODEX_SESSION,
        }},
        {"timestamp": TS, "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "id": "ctc-one",
            "call_id": "call-one", "input": wrapper,
        }},
        {"timestamp": "2026-07-09T12:00:02Z", "type": "response_item", "payload": {
            "type": "custom_tool_call_output", "call_id": "call-one", "output": [
                {"type": "input_text", "text":
                 "Script completed\n  0.88 [lex] zeta_surface\nraw output must not survive"},
            ],
        }},
        {"timestamp": TS, "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "id": "ctc-single",
            "call_id": "call-single", "input":
            "tools.shell_command({command: 'Get-Content -LiteralPath \"%s\\lambda_memory.md\"'});" % MEM,
        }},
        # Searching for the words "brain.py recall" is not itself a recall invocation.
        {"timestamp": TS, "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "id": "ctc-mention",
            "call_id": "call-mention", "input":
            "tools.shell_command({command: %s});" % json.dumps(
                "rg -n \"brain.py recall\" \"%s\"" % MEM
            ),
        }},
        # An apply-patch wrapper contains a .md name but is not a content read and must stay absent.
        {"timestamp": TS, "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "id": "ctc-patch",
            "call_id": "call-patch", "input":
            "text(await tools.apply_patch('*** Add File: ~/.claude/projects/"
            "{{BRAIN_SLUG}}/memory/kappa_not_read.md'));",
        }},
        {"timestamp": "2099-01-01T00:00:00Z", "type": "response_item", "payload": {
            "type": "custom_tool_call", "name": "exec", "id": "ctc-future",
            "call_id": "call-future", "input": json.dumps({
                "command": "Get-Content -LiteralPath \"%s\\future_codex_not_accessed.md\"" % MEM,
            }),
        }},
    ]
    _dump(path, rows)


def _nested_claude_fixture(path):
    _dump(path, [{
        "timestamp": TS,
        "sessionId": CLAUDE_SESSION,
        "message": {"content": [{
            "type": "tool_use", "id": "nested-read", "name": "Read",
            "input": {"file_path": MEM + r"\nested_subagent_memory.md"},
        }]},
    }])


def _surface_fixture(path):
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("ts\tsession\tslug\tsrc\n")
        fh.write("%.0f\thook-session\teta_hook_surface\tspread\n" % (EPOCH + 3))
        fh.write("%.0f\thook-session\ttheta_unknown_surface\tprivate-source-label-must-not-leak\n" %
                 (EPOCH + 4))
        fh.write("4102444800\thook-session\tfuture_hook_not_accessed\tspread\n")


def _read_tsv(path):
    with io.open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # Discovery is not execution: this script writes, so a help request must
        # exit before any work. See script_help_flag_can_execute_work.
        print(__doc__ or os.path.basename(__file__))
        return
    checks = 0
    with tempfile.TemporaryDirectory(prefix="memory-access-eval-") as td:
        claude_root = os.path.join(td, "claude")
        codex_root = os.path.join(td, "codex")
        os.makedirs(os.path.join(claude_root, "parent", "subagents"))
        os.makedirs(codex_root)
        claude = os.path.join(claude_root, "claude.jsonl")
        nested_claude = os.path.join(claude_root, "parent", "subagents", "agent-one.jsonl")
        codex = os.path.join(codex_root, "codex.jsonl")
        surfaced = os.path.join(td, "surfaced.tsv")
        out = os.path.join(td, "memory_access.tsv")
        legacy = os.path.join(td, "retrievals.tsv")
        _claude_fixture(claude)
        _nested_claude_fixture(nested_claude)
        _codex_fixture(codex)
        _surface_fixture(surfaced)

        rows, stats = access.build_records(
            [claude], [codex], surfaced, cutoff=EPOCH - 60, end=EPOCH + 60
        )
        rows2, stats2 = access.build_records(
            [claude], [codex], surfaced, cutoff=EPOCH - 60, end=EPOCH + 60
        )
        assert rows == rows2 and stats == stats2
        checks += 1

        by_kind = {}
        for row in rows:
            by_kind.setdefault(row["access_kind"], []).append(row)
        assert {r["slug"] for r in by_kind["read_explicit"]} == {"alpha_memory"}
        assert {r["slug"] for r in by_kind["read_command"]} == {
            "beta_memory", "chain_memory", "delta_memory", "lambda_memory",
        }
        assert {r["slug"] for r in by_kind["search_command"]} == {
            "", "epsilon_search", "theta_search_only",
        }
        assert {r["slug"] for r in by_kind["recall_surface"]} == {
            "gamma_surface", "second_surface", "zeta_surface",
        }
        assert len(by_kind["recall_query"]) == 2
        assert {r["slug"] for r in by_kind["hook_surface"]} == {
            "eta_hook_surface", "theta_unknown_surface",
        }
        checks += 1

        all_slugs = {r["slug"] for r in rows}
        read_slugs = {r["slug"] for kind in ("read_explicit", "read_command") for r in by_kind[kind]}
        assert "theta_not_read" not in all_slugs and "iota_not_read" not in all_slugs
        assert "kappa_not_read" not in all_slugs
        assert "mention_not_read" not in all_slugs
        assert not any("not_accessed" in slug for slug in all_slugs)
        assert "theta_search_only" not in read_slugs
        assert all(r["access_kind"] != "read_command" for r in by_kind["search_command"])
        assert stats["claude_future_ts"] == 1 and stats["codex_future_ts"] == 1
        assert stats["surface_future_ts"] == 1
        checks += 1

        assert all(r["actor"] == "claude" for r in rows if "claude" in r["source"])
        assert all(r["actor"] == "codex" for r in rows if "codex" in r["source"])
        assert all(r["actor"] == "hook" for r in by_kind["hook_surface"])
        assert {r["source"] for r in by_kind["hook_surface"]} == {
            "hook_spread", "hook_unknown",
        }
        assert access._extract_commands({"command": "Get-Content x"}) == ["Get-Content x"]
        assert access._extract_commands(
            "tools.shell_command({command: 'Get-Content x'})"
        ) == ["Get-Content x"]
        assert access._split_statements('Write-Output "a;b" && Get-Content x') == [
            'Write-Output "a;b"', "Get-Content x",
        ]
        checks += 1

        # Claude's delegated agents are nested below the project directory but preserve
        # the parent session ID. Discovery must include them exactly once and attribution
        # must remain Claude rather than inventing a new actor/session.
        discovered = access._discover(claude_root, True, cutoff=None)
        assert discovered == sorted([claude, nested_claude])
        assert access._discover(claude_root, False, cutoff=None) == [claude]
        nested_rows, nested_stats = access.scan_claude(
            [nested_claude], cutoff=EPOCH - 60, end=EPOCH + 60
        )
        assert len(nested_rows) == 1
        assert nested_rows[0]["slug"] == "nested_subagent_memory"
        assert nested_rows[0]["actor"] == "claude"
        assert nested_rows[0]["session"] == CLAUDE_SESSION
        assert nested_stats["claude_files"] == 1
        checks += 1

        assert stats["duplicates"] >= 3
        ids = [r["event_id"] for r in rows]
        assert len(ids) == len(set(ids)) and ids == [r["event_id"] for r in rows2]
        checks += 1

        access._atomic_write(out, rows)
        written = _read_tsv(out)
        assert written == rows
        blob = io.open(out, encoding="utf-8").read()
        for forbidden in (
            "SUPERSECRET", "NEEDLE-RAW", "PIPELINE-SEARCH", "RAW-SEARCH", "RAW-CODEX", "description must",
            "raw output must", "private-source-label", "mention_not_read",
        ):
            assert forbidden not in blob
        checks += 1

        # A failed promotion must preserve the previous production bytes and clean its temp file.
        old = io.open(out, "rb").read()
        real_replace = access.os.replace
        try:
            def fail_replace(_src, _dst):
                raise OSError("intentional atomic promotion failure")
            access.os.replace = fail_replace
            try:
                access._atomic_write(out, rows[:1])
                raise AssertionError("failed replace unexpectedly returned")
            except OSError as exc:
                assert "intentional" in str(exc)
        finally:
            access.os.replace = real_replace
        assert io.open(out, "rb").read() == old
        assert not os.path.exists(out + ".tmp.%d" % os.getpid())
        checks += 1

        with io.open(legacy, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("slug\tepoch\tiso\tsession\n")
            fh.write("alpha_memory\t%.0f\t%s\t%s\n" % (EPOCH, TS, CLAUDE_SESSION))
        report = access.summarize(rows, stats, legacy, cutoff=None)
        assert report["retrievals_compat"]["matched"] == 1
        assert report["command_only_slugs_beyond_explicit"] == 4
        checks += 1

        # Scheduled writes fail closed: a missing transcript root must neither produce nor
        # replace an output file. Invalid lookback windows are also rejected by the CLI parser.
        missing_out = os.path.join(td, "must-not-exist.tsv")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            rc = access.main([
                "--claude-dir", os.path.join(td, "missing-claude"),
                "--codex-dir", codex_root,
                "--surfaced", surfaced,
                "--retrievals", legacy,
                "--out", missing_out,
                "--write",
            ])
        assert rc == 2 and not os.path.exists(missing_out)
        assert "required transcript root missing" in captured.getvalue()
        for invalid_days in ("-1", "nan", "inf"):
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    access._args(["--days", invalid_days])
                    raise AssertionError("invalid --days unexpectedly accepted: " + invalid_days)
                except SystemExit as exc:
                    assert exc.code == 2
        checks += 1

        # Schema is deliberately metadata-only and every non-read class stays distinguishable.
        assert tuple(written[0].keys()) == access.FIELDS
        assert all(float(r["confidence"]) > 0 for r in written)
        assert all(len(r["evidence_sha256"]) == 64 for r in written)
        checks += 1

    print("eval_memory_access: PASS (%d isolated checks; synthetic Claude/Codex/hook fixtures)" % checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
